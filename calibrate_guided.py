"""The automatic half of `calibrate.py --guided` (PR 23): look through a recording without a profile and
find what the calibration page needs, so the owner only confirms or fixes it.

  scan()         about 120 small frames spread over the recording, each grabbed by seeking (so an hour of video costs
                 the same as ten minutes). A frame is the picture at 1/4 size, point-sampled: nothing is blended, so
                 a thin coloured bar keeps its colour and a white star keeps its edge.
  coarse_bars()  the coloured bars by the minimap: thin, level strips whose colour is the same on many frames while
                 the scene behind them changes. It only says roughly where they are; calibrate.find_strips() then
                 finds them properly at full size inside that box.
  coarse_stars() the wanted stars: white shapes whose EDGES sit on exactly the same pixels in several frames, but not
                 in all of them. The scene's own edges move from frame to frame; a HUD shape that comes and goes
                 does not. It gives a rough box and which frames show it; calibrate.fit_stars() then fits the outline.
  choose_frames()  about a dozen frames for the page: HUD shown, HUD hidden, stars yes.
  check_window()   the 3 minutes the finished profile is tried on (round the stars, when there are any).

Nothing here knows GTA V: no position, colour or size of any HUD part is assumed. Standard library only.
"""

import functools
import json
import math
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import calibrate as cal
import clip

SCAN_FRAMES = 120        # looks at the recording (fewer for a short one: never closer than SCAN_MIN_GAP_S)
SCAN_MIN_GAP_S = 0.5
SCAN_WORKERS = 6
DIV = 4                  # a small frame is the picture at 1/DIV size
BLACK_MEAN = 14.0        # a small frame whose average channel value is under this is a black screen

# coarse_bars. Judgement values, set on the GTA V recordings and on calibguidedtest.py's made-up HUDs.
BARS_USE = 40            # at most this many frames (spread evenly) go into the search
HUE_BUCKETS = 12         # 30 degrees each
BAR_SHARE = 0.25         # a bar pixel has one hue on at least this share of the frames (the HUD may be off on the rest)
BAR_MIN_FRAMES = 5       # ...and on at least this many: with a handful of frames, chance alone repeats a hue
BAR_MIN_RUN = 5          # small pixels: a bar is at least this long (20 px of the real picture)
BAR_THICK = 4            # small pixels: a bar is at most this thick (16 px of the real picture)
BAR_PAD = (16, 10)       # real pixels added left/right and above/below the strips for the rough box

# coarse_stars
STAR_BRIGHT = 185        # every channel at least this: white or light grey
STAR_DARK = 130          # every channel at most this: clearly not white
STAR_MIN_FRAMES = 3      # an edge pixel must be there on this many frames...
STAR_SIGMAS = 4.0        # ...and on this many standard deviations more frames than a usual pixel of the picture...
STAR_MAX_SHARE = 0.8     # ...and on no more than this share of the HUD frames (else it is always on: not the stars)
STAR_JOIN = 2            # small pixels: edge pixels this close together are one shape
STAR_MIN_PIXELS = 8      # a shape with fewer steady edge pixels is noise
STAR_ON = 0.2            # a frame shows the shape when this share of its edge pixels are edges there
STAR_OFF = 0.04          # ...and surely doesn't when at most this share are
STAR_WIDEN = 5.5         # the row may reach this many box-heights past the lit stars on each side (empty slots)
STAR_DIFF_TIMES = 2.0    # an empty slot: with/without difference this many times the usual one further out...
STAR_DIFF_MIN = 25.0     # ...and at least this much (brightness, 0-255)
STAR_DIFF_GAP = 2        # small pixels below that which don't end the row yet (the gap between two stars)
STAR_ALIKE = 0.5         # a frame looks this much like the frames that show the shape (look_alike): it shows it too
STAR_UNLIKE = 0.2        # ...and at most this much: it surely doesn't
STAR_PAD = (8, 6)        # real pixels of space left/right and above/below the stars in the star box
STAR_TRY = 10            # the best this many shapes get a real star fit; the most star-like one wins
STAR_MAX_ASPECT = 10     # a box more than this many times wider than tall is a line of text, not a star row
STAR_MIN_HEIGHT = 16     # real pixels: smaller is too small for calibrate.fit_stars
STAR_FIT_YES, STAR_FIT_NO = 4, 6   # full-size pictures with and without the shape that go into the fit
# GTA 6 (launch-fixes): no coloured bars by the minimap. Its player arrow (a white pointer in a dark disc, about 17 px
# across at 1080p) is looked for in the bottom-left part of the picture, and lit red stars in the top-right part. The
# areas are shares of the picture (x, y, w, h); nothing else about GTA 6's layout is assumed.
BARS_MIN_Y = 0.5          # GTA V's minimap bars are in the lower half of the picture
ARROW_REGION = (0.0, 0.68, 0.30, 0.32)
ARROW_LOOK = 40           # at most this many frames are looked at (spread over the recording)
ARROW_BRIGHT, ARROW_DARK = 170, 95   # brightness (0-255) of the pointer, and of the disc round it
ARROW_CORE_PX = 6         # bright pixels within 3 px of the spot, at least
ARROW_RING_SHARE = 0.55   # of the pixels 6-7 px from the spot, at least this share dark
ARROW_MIN_SHARE = 0.25    # the arrow's spot must pass in at least this share of the frames looked at
STAR6_REGION = (0.72, 0.0, 0.28, 0.12)
STAR6_MIN_PX = 40         # a lit red star is a group of at least this many steady red pixels
TUNE_REACH = (1.0, 5.0)  # tune_outline tries outlines this much smaller and larger (real pixels)...
TUNE_STEP = 0.5          # ...in steps of this
STAR_MARGIN = 0.25       # the fitted outline must score this much higher on every "stars" picture than on any other


def small_size(info):
    w, h = cal.shown_size(info)
    return w // DIV, h // DIV


def scan_times(duration, n=SCAN_FRAMES, min_gap=SCAN_MIN_GAP_S):
    """Evenly spread times, a little in from both ends."""
    a, b = min(1.0, duration * 0.02), duration - min(1.5, duration * 0.02)
    n = max(3, min(n, int((b - a) / min_gap) + 1))
    return [round(a + (b - a) * k / (n - 1), 2) for k in range(n)]


def grab_small(ffmpeg, video, info, t, size):
    """One small RGB frame (bytes) at t, point-sampled, with clip.py's range handling; None if ffmpeg can't."""
    conv = "in_color_matrix=bt709:in_range=tv:" if cal.range_fix(info) else ""
    out = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video),
                          "-frames:v", "1", "-vf",
                          f"scale={size[0]}:{size[1]}:flags=neighbor:{conv}out_range=pc,format=rgb24",
                          "-f", "rawvideo", "-"], capture_output=True)
    return out.stdout if out.returncode == 0 and len(out.stdout) == size[0] * size[1] * 3 else None


def scan(ffmpeg, video, info, times=None, grab=grab_small):
    """[(time, small RGB frame)] over the recording; times that can't be read are left out."""
    size = small_size(info)
    times = times or scan_times(info["duration"])
    with ThreadPoolExecutor(SCAN_WORKERS) as pool:
        raws = list(pool.map(lambda t: grab(ffmpeg, video, info, t, size), times))
    return [(t, raw) for t, raw in zip(times, raws) if raw]


def is_black(raw):
    return sum(raw) / len(raw) < BLACK_MEAN


def spread(items, n):
    """n items spread evenly over the list (all of them if there are fewer)."""
    if n <= 0:
        return []
    if len(items) <= n:
        return list(items)
    return [items[round((k + 0.5) * len(items) / n - 0.5)] for k in range(n)]


# --- The bars ---

def _hue_bucket(r, g, b):
    return int(cal.hue((r, g, b)) / (360 / HUE_BUCKETS)) % HUE_BUCKETS


def bar_pixels(images, w, h):
    """Per pixel: (hue bucket, mean colour) when one hue is there on at least BAR_SHARE of the frames, else None."""
    need = max(BAR_MIN_FRAMES, math.ceil(BAR_SHARE * len(images)))
    planes = [[im[k::3] for im in images] for k in range(3)]
    out = [None] * (w * h)
    for p, (rs, gs, bs) in enumerate(zip(zip(*planes[0]), zip(*planes[1]), zip(*planes[2]))):
        seen = {}
        for r, g, b in zip(rs, gs, bs):
            if max(r, g, b) - min(r, g, b) >= cal.BAR_CHROMA:
                seen.setdefault(_hue_bucket(r, g, b), []).append((r, g, b))
        if not seen:
            continue
        bucket = max(seen, key=lambda k: len(seen[k]))
        if len(seen[bucket]) >= need:
            cols = seen[bucket]
            out[p] = (bucket, tuple(sum(c[k] for c in cols) / len(cols) for k in range(3)))
    return out


def _near(a, b):
    return min((a - b) % HUE_BUCKETS, (b - a) % HUE_BUCKETS) <= 1


def coarse_bars(frames, w, h):
    """Roughly where the coloured bars are, from small frames (bytes, w x h): {"box": [x, y, w, h] in small pixels,
    "strips": [{"x": [x0, x1], "y": [y0, y1], "colour": (r, g, b)}, ...]} or None when no row of 2 or more thin
    coloured strips is found."""
    images = spread([f for f in frames if not is_black(f)], BARS_USE)
    if len(images) < BAR_MIN_FRAMES:
        return None
    px = bar_pixels(images, w, h)

    def same(x, y, bucket):
        v = px[y * w + x] if 0 <= y < h else None
        return v is not None and _near(v[0], bucket)

    runs = []   # (y, x0, x1, bucket): a level run of one hue that is thin and has nothing of its hue near it
    for y in range(h):
        x = 0
        while x < w:
            v = px[y * w + x]
            if v is None:
                x += 1
                continue
            x0, bucket = x, v[0]
            while x < w and px[y * w + x] is not None and _near(px[y * w + x][0], bucket):
                x += 1
            if x - x0 < BAR_MIN_RUN:
                continue
            # A bar's hue is gone BAR_THICK rows above and below it (the edge of the sky fails this), and the patch
            # round the run is thin: few of the rows near it have the hue along most of the run (the middle of a
            # patch 5-7 rows tall fails this).
            above = sum(same(i, y - BAR_THICK, bucket) for i in range(x0, x))
            below = sum(same(i, y + BAR_THICK, bucket) for i in range(x0, x))
            thick = sum(1 for dy in range(-BAR_THICK, BAR_THICK + 1)
                        if sum(same(i, y + dy, bucket) for i in range(x0, x)) >= 0.7 * (x - x0))
            if max(above, below) <= 0.3 * (x - x0) and thick <= BAR_THICK:
                runs.append((y, x0, x, bucket))
    # Runs on neighbouring rows that overlap are one strip; strips level with each other are one row of bars.
    strips = []
    for y, x0, x1, bucket in runs:
        for s in strips:
            if s["y"][1] == y and _near(s["bucket"], bucket) and min(s["x"][1], x1) - max(s["x"][0], x0) >= BAR_MIN_RUN - 1:
                s["y"][1] = y + 1
                s["x"] = [min(s["x"][0], x0), max(s["x"][1], x1)]
                break
        else:
            if not any(s["y"][0] <= y < s["y"][1] and _near(s["bucket"], bucket)
                       and min(s["x"][1], x1) > max(s["x"][0], x0) for s in strips):
                strips.append({"x": [x0, x1], "y": [y, y + 1], "bucket": bucket})
    strips = [s for s in strips if s["y"][1] - s["y"][0] <= BAR_THICK]
    best = None
    for s in strips:
        level = sorted((t for t in strips if t["y"][0] < s["y"][1] + 1 and s["y"][0] < t["y"][1] + 1),
                       key=lambda t: t["x"][0])
        row = []
        for t in level:                                  # side by side only
            if not row or t["x"][0] >= row[-1]["x"][1]:
                row.append(t)
        if len(row) >= 2:
            length = sum(t["x"][1] - t["x"][0] for t in row)
            # Bars sit close together: a row's strips are judged with the gaps between them counted against it.
            span = row[-1]["x"][1] - row[0]["x"][0]
            score = length - 0.5 * (span - length)
            if best is None or score > best[0]:
                best = (score, row)
    if best is None:
        return None
    row = best[1]
    for s in row:
        cols = [px[y * w + x][1] for y in range(*s["y"]) for x in range(*s["x"]) if px[y * w + x] is not None]
        s["colour"] = tuple(sum(c[k] for c in cols) / len(cols) for k in range(3))
    x0, x1 = row[0]["x"][0], row[-1]["x"][1]
    y0, y1 = min(s["y"][0] for s in row), max(s["y"][1] for s in row)
    return {"box": [x0, y0, x1 - x0, y1 - y0],
            "strips": [{"x": s["x"], "y": s["y"], "colour": s["colour"]} for s in row]}


def hud_shown(raw, w, bars):
    """True when a small frame has the coarse bars' colours where the bars are (all of them but one, at least 2)."""
    here = 0
    for s in bars["strips"]:
        ref = cal.from_grey(s["colour"])
        here += cal.same_hue(cal.area_mean(raw, w, s["x"], s["y"]), ref, math.hypot(*ref))
    return here >= max(2, len(bars["strips"]) - 1)


def full_rect(box, pad=(0, 0)):
    """A small-pixel box as a real-pixel one, with real pixels of padding."""
    x, y, w, h = box
    return [x * DIV - pad[0], y * DIV - pad[1], w * DIV + 2 * pad[0], h * DIV + 2 * pad[1]]


# --- The stars ---

_BRIGHT = bytes(1 if v >= STAR_BRIGHT else 0 for v in range(256))
_DARK = bytes(1 if v <= STAR_DARK else 0 for v in range(256))


def _mask(raw, table):
    """The pixels whose three channels all pass the table, as one big number: byte p is 1 where pixel p passes.
    (Whole-frame arithmetic on big numbers is how this stays fast without numpy.)"""
    planes = [int.from_bytes(raw[k::3].translate(table), "little") for k in range(3)]
    return planes[0] & planes[1] & planes[2]


def bright_mask(raw):
    return _mask(raw, _BRIGHT)


def edge_mask(raw, w):
    """Where a white pixel has a clearly darker pixel right next to it (left, right, above or below): the edge of a
    white HUD shape with its dark outline, or of anything white on something dark."""
    b, d = _mask(raw, _BRIGHT), _mask(raw, _DARK)
    return ((b & (d >> 8)) | (b & (d << 8)) | (b & (d >> (8 * w))) | (b & (d << (8 * w)))) & _inside(w, len(raw) // 3 // w)


@functools.lru_cache(maxsize=4)
def _inside(w, h):
    """1 for every pixel but the picture's outermost ring (where a shifted mask wraps round to the next row)."""
    row = bytes([0]) + bytes([1]) * (w - 2) + bytes([0])
    return int.from_bytes(bytes(w) + row * (h - 2) + bytes(w), "little")


def star_shapes(frames, w, h):
    """Every shape whose white edges sit on the same pixels in several frames but not in all: [{"box", "pixels",
    "share": per frame, the share of its edge pixels that are edges there, "score"}], best first. A HUD shape is
    either there or not, so frames in between count against a shape; bigger and crisper wins."""
    n = len(frames)
    if n < 2 * STAR_MIN_FRAMES or n > 255:
        return []
    edges = [edge_mask(f, w) for f in frames]
    count = sum(edges).to_bytes(w * h + 8, "little")[:w * h]      # per pixel: on how many frames it is an edge
    mean = sum(count) / len(count)
    sd = math.sqrt(sum(c * c for c in count) / len(count) - mean * mean)
    low, top = max(STAR_MIN_FRAMES, math.ceil(mean + STAR_SIGMAS * sd)), STAR_MAX_SHARE * n
    left = {p for p in range(w * h) if low <= count[p] <= top}
    as_bytes = [e.to_bytes(w * h + 8, "little") for e in edges]
    shapes = []
    while left:                                   # join steady pixels that are close together into shapes
        stack, shape = [left.pop()], []
        while stack:
            p = stack.pop()
            shape.append(p)
            x, y = p % w, p // w
            for dy in range(-STAR_JOIN, STAR_JOIN + 1):
                for dx in range(-STAR_JOIN, STAR_JOIN + 1):
                    q = (y + dy) * w + x + dx
                    if 0 <= x + dx < w and q in left:
                        left.remove(q)
                        stack.append(q)
        if len(shape) < STAR_MIN_PIXELS:
            continue
        share = [sum(e[p] for p in shape) / len(shape) for e in as_bytes]
        on = [s for s in share if s >= STAR_ON]
        off = sum(1 for s in share if s <= STAR_OFF)
        if len(on) < STAR_MIN_FRAMES or off < STAR_MIN_FRAMES:
            continue
        clear = (len(on) + off) / n
        xs, ys = [p % w for p in shape], [p // w for p in shape]
        shapes.append({"box": [min(xs), min(ys), max(xs) + 1 - min(xs), max(ys) + 1 - min(ys)], "pixels": len(shape),
                       "share": share, "on": len(on), "off": off,
                       "score": len(shape) * clear * clear * (sum(on) / len(on))})
    return sorted(shapes, key=lambda s: -s["score"])


def _lum(raw, i):
    return 0.2126 * raw[i] + 0.7152 * raw[i + 1] + 0.0722 * raw[i + 2]


def look_alike(frames, shape, w, h):
    """Per frame, -1 to 1: how much its picture inside the shape's box looks like the frames that surely show the
    shape (the brightness pattern, whatever its strength). Edges alone miss a frame where the stars flash grey or
    sit on a white sky; the pattern doesn't, so this is what sorts the frames into 'stars' and 'no stars'."""
    x0, y0, bw, bh = shape["box"]
    cells = [(y * w + x) * 3 for y in range(max(0, y0 - 1), min(h, y0 + bh + 1))
             for x in range(max(0, x0 - 1), min(w, x0 + bw + 1))]

    def unit(f):
        v = [_lum(f, i) for i in cells]
        m = sum(v) / len(v)
        v = [a - m for a in v]
        length = math.sqrt(sum(a * a for a in v)) or 1.0
        return [a / length for a in v]
    vecs = [unit(f) for f in frames]
    sure = [v for v, sh in zip(vecs, shape["share"]) if sh >= STAR_ON]
    usual = [sum(col) / len(sure) for col in zip(*sure)]
    length = math.sqrt(sum(a * a for a in usual)) or 1.0
    return [sum(a * b for a, b in zip(v, usual)) / length for v in vecs]


def star_extent(frames, shape, alike, w, h):
    """The whole star row's box in small pixels, empty slots included. The shape found is only the stars that were
    lit. An empty slot isn't white, but it is still drawn over the scene, so along the row the average picture WITH
    the stars differs from the average picture WITHOUT them: the box grows sideways while that difference stays
    clearly above what the scene alone gives further out (at most STAR_WIDEN box-heights each way)."""
    x0, y0, bw, bh = shape["box"]
    yes = [f for f, a in zip(frames, alike) if a >= STAR_ALIKE]
    no = [f for f, a in zip(frames, alike) if a <= STAR_UNLIKE]
    if not yes or not no:
        return list(shape["box"])
    rows = range(max(0, y0 - 1), min(h, y0 + bh + 1))
    reach = round(STAR_WIDEN * bh)
    lo, hi = max(0, x0 - 2 * reach), min(w, x0 + bw + 2 * reach)
    diff = {x: max(abs(sum(_lum(f, (y * w + x) * 3) for f in yes) / len(yes)
                       - sum(_lum(f, (y * w + x) * 3) for f in no) / len(no)) for y in rows) for x in range(lo, hi)}
    outside = sorted(v for x, v in diff.items() if x < x0 - 1 or x >= x0 + bw + 1)
    floor = max(STAR_DIFF_MIN, STAR_DIFF_TIMES * outside[len(outside) // 2]) if outside else STAR_DIFF_MIN
    left, gap = x0, 0
    for x in range(x0 - 1, max(lo, x0 - reach) - 1, -1):
        gap = 0 if diff[x] >= floor else gap + 1
        if gap > STAR_DIFF_GAP:
            break
        if not gap:
            left = x
    right, gap = x0 + bw, 0
    for x in range(x0 + bw, min(hi, x0 + bw + reach)):
        gap = 0 if diff[x] >= floor else gap + 1
        if gap > STAR_DIFF_GAP:
            break
        if not gap:
            right = x + 1
    return [left, y0, right - left, bh]


def star_rect(box, frame):
    """A small-pixel star box as the full-size box a person would drag: a little space all round, inside the frame."""
    x, y, w, h = full_rect(box, STAR_PAD)
    x0, x1, y0, y1 = max(0, x), min(frame[0], x + w), max(0, y), min(frame[1], y + h)
    return [x0, y0, x1 - x0, y1 - y0]


def grab_area(ffmpeg, video, info, t, rect):
    """rect [x, y, w, h] of the full-size frame at t as RGB bytes, read the way calibrate.Session.read_area does."""
    x, y, w, h = rect
    conv = "in_color_matrix=bt709:in_range=tv:" if cal.range_fix(info) else ""
    out = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(video),
                          "-frames:v", "1", "-vf", f"crop={w}:{h}:{x}:{y},scale={w}:{h}:{conv}out_range=pc,format=rgb24",
                          "-f", "rawvideo", "-"], capture_output=True)
    return out.stdout if out.returncode == 0 and len(out.stdout) == w * h * 3 else None


def row_rect(fit, frame):
    """The star box a fitted row asks for: the row itself with STAR_PAD round it, inside the frame."""
    r = fit["radius"]
    x0 = math.floor(fit["first_tip_x"] - r * cal.COS18 - STAR_PAD[0])
    x1 = math.ceil(fit["first_tip_x"] + fit["tip_spacing"] * (fit["slots"] - 1) + r * cal.COS18 + STAR_PAD[0])
    y0 = math.floor(fit["tip_y"] - STAR_PAD[1])
    y1 = math.ceil(fit["tip_y"] + r * (1 + cal.SIN54) + STAR_PAD[1])
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(frame[0], x1), min(frame[1], y1)
    return [x0, y0, x1 - x0, y1 - y0]


def find_stars(on, w, h, frame, read, slots=None, log=lambda m: None):
    """The wanted stars, found without being told where they are. on: [(time, small RGB)] of frames with the HUD on
    screen; read(time, rect) gives that full-size area as RGB bytes (or None); slots: how many star slots the game
    has, when the owner knows (else the fit counts them, and can only count the ones it sees lit or drawn empty).

    Each of the best few shapes that come and go (star_shapes) is tried as the star row: its frames are sorted into
    "stars" and "no stars" (look_alike), the row's whole width is found (star_extent), and calibrate.fit_stars fits
    star outlines to full-size pictures of both kinds. The winner is the shape where a real star outline fits and
    tells the two kinds apart by the widest margin; text and numbers on the HUD fit a star badly. Returns None when
    no shape passes (no wanted stars in this recording, as far as the tool can tell), else
    {"fit": calibrate's fit (not yet confirmed), "yes": [times], "no": [times], "alike": {time: -1..1}}."""
    raws = [r for _, r in on]
    best = None
    tried = 0
    for shape in star_shapes(raws, w, h):
        if tried == STAR_TRY:
            break
        alike = look_alike(raws, shape, w, h)
        rect = star_rect(star_extent(raws, shape, alike, w, h), frame)
        if rect[2] > STAR_MAX_ASPECT * rect[3] or rect[3] < STAR_MIN_HEIGHT:
            continue                                    # a line of text, or too small to be a row of stars
        yes_i = [i for i, a in enumerate(alike) if a >= STAR_ALIKE]
        no_i = [i for i, a in enumerate(alike) if a <= STAR_UNLIKE]
        if len(yes_i) < cal.STAR_MIN_YES or len(no_i) < cal.STAR_MIN_NO:
            continue
        tried += 1
        # The frames with the most of the row lit teach the fit the most; then spread them over the recording.
        most = sorted(sorted(yes_i, key=lambda i: -shape["share"][i])[:2 * STAR_FIT_YES])
        yes_i, no_i = spread(most, STAR_FIT_YES), spread(no_i, STAR_FIT_NO)
        try:
            fit = _fit(read, on, yes_i, no_i, rect, None)
            if fit and fit["separates"]:                # once more in the box the row itself asks for
                tight = row_rect(fit, frame)
                again = _fit(read, on, yes_i, no_i, tight, fit["slots"]) if tight != rect else None
                if again and again["separates"]:
                    fit = again
        except cal.CalibError as e:
            log(f"    a shape at {rect} is no star row ({str(e)[:60]})")
            continue
        if not fit:
            continue
        margin = fit["lowest_yes"] - fit["highest_no"]
        star_like = cal.STAR_INNER[0] + 0.02 < fit["inner"] < cal.STAR_INNER[1] - 0.02
        log(f"    shape at {fit['box']}: {fit['slots']} star outline(s) fit, pictures with it score {fit['lowest_yes']:.2f} "
            f"or more, without it {fit['highest_no']:.2f} or less" + ("" if star_like else " (not star-shaped)"))
        if fit["separates"] and star_like and margin >= STAR_MARGIN and (best is None or margin > best[0]):
            best = (margin, fit, alike, rect, yes_i, no_i)
    if best is None:
        return None
    _, fit, alike, rect, yes_i, no_i = best
    if slots and fit["slots"] != slots:
        # The owner said how many slots the row has. The slots the fit didn't see may be on either side of the ones
        # it did, so the box grows by that many star widths both ways (the fit's own spacing, or a little more than
        # a star's width when it saw one star only) and the fit is told the number.
        pitch = fit["tip_spacing"] if fit["slots"] > 1 else 2.2 * fit["radius"]
        extra = abs(slots - fit["slots"])
        grow = math.ceil(extra * pitch + fit["radius"])
        x0, y0, bw, bh = fit["box"]
        left = max(0, x0 - grow)
        wide = [left, y0, min(frame[0], x0 + bw + grow) - left, bh]
        try:
            told = _fit(read, on, yes_i, no_i, wide, slots)
            if told and told["separates"]:
                # The fit can sit one slot off: an outline on nothing costs it little. Where the slots really are
                # is settled on all the scan's frames (slot_shift), then the fit is redone in that row's own box.
                shift = slot_shift(raws, alike, told, extra, w, h)
                moved = dict(told, first_tip_x=told["first_tip_x"] + shift * told["tip_spacing"])
                tight = row_rect(moved, frame)
                again = _fit(read, on, yes_i, no_i, tight, slots) if tight != wide else None
                fit = again if again and again["separates"] else told
                log(f"    with {slots} slots, as you said: the row is at {fit['box']}")
            else:
                log(f"    {slots} slots, as you said, don't fit what the pictures show; keeping the {fit['slots']} found")
        except cal.CalibError:
            log(f"    {slots} slots, as you said, don't fit in the row's box; keeping the {fit['slots']} found")
    roomy = row_rect(dict(fit, radius=fit["radius"] + TUNE_REACH[1], tip_y=fit["tip_y"] - TUNE_REACH[1]), frame)
    try:
        again = _fit(read, on, yes_i, no_i, roomy, fit["slots"]) if roomy != fit["box"] else fit
        if again and again["separates"]:
            fit = again
    except cal.CalibError:
        pass
    fit = tune_outline(fit, fit["_yes"], fit["_no"])
    if fit.get("tuned_for_count"):
        log(f"    outline resized to radius {fit['radius']:g} px so the star count can see each star's dark rim")
    fit = {k: v for k, v in fit.items() if not k.startswith("_")}
    return {"fit": fit, "alike": {t: round(a, 3) for (t, _), a in zip(on, alike)},
            "yes": [t for (t, _), a in zip(on, alike) if a >= STAR_ALIKE],
            "no": [t for (t, _), a in zip(on, alike) if a <= STAR_UNLIKE]}


def slot_shift(raws, alike, fit, reach, w, h):
    """By how many slots (-reach to reach) a fitted row should move sideways to sit on the slots that are really
    drawn. A slot, lit or empty, is drawn over the scene, so at its middle the average small frame WITH the stars
    differs from the average one WITHOUT; where the row has no slot they are alike. The shift with the largest
    difference summed over the row's slot middles wins; 0 unless another is clearly better."""
    yes = [f for f, a in zip(raws, alike) if a >= STAR_ALIKE]
    no = [f for f, a in zip(raws, alike) if a <= STAR_UNLIKE]
    if not yes or not no:
        return 0
    cy = round((fit["tip_y"] + fit["radius"]) / DIV)

    def drawn(x):
        cx = round(x / DIV)
        cells = [((cy + dy) * w + cx + dx) * 3 for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                 if 0 <= cx + dx < w and 0 <= cy + dy < h]
        if not cells:
            return 0.0
        return sum(abs(sum(_lum(f, i) for f in yes) / len(yes) - sum(_lum(f, i) for f in no) / len(no))
                   for i in cells) / len(cells)

    def total(shift):
        return sum(drawn(fit["first_tip_x"] + (k + shift) * fit["tip_spacing"]) for k in range(fit["slots"]))
    best = max(range(-reach, reach + 1), key=lambda k: (total(k), -abs(k)))
    return best if total(best) > 1.05 * total(0) else 0


def _fit(read, on, yes_i, no_i, rect, slots):
    """calibrate.fit_stars on full-size pictures of rect; the fit also carries those pictures ("_yes", "_no")
    until find_stars is done with it. None when too few pictures could be read."""
    with ThreadPoolExecutor(SCAN_WORKERS) as pool:
        yes = list(pool.map(lambda i: read(on[i][0], rect), yes_i))
        no = list(pool.map(lambda i: read(on[i][0], rect), no_i))
    yes, no = [r for r in yes if r], [r for r in no if r]
    if len(yes) < cal.STAR_MIN_YES or len(no) < cal.STAR_MIN_NO:
        return None
    return {**cal.fit_stars(rect, yes, no, slots), "_yes": yes, "_no": no}


def _star_prof(box, geom):
    """Just enough of a profile for index.py's star counting and clip.py's star score, from a box and an outline."""
    prof = SimpleNamespace(star_box=tuple(box), hud_w=box[2], star_tip_y=geom["tip_y"], star_radius=geom["radius"],
                           star_inner=geom["inner"], data={},
                           star_tips_x=tuple(geom["first_tip_x"] + geom["tip_spacing"] * k for k in range(geom["slots"])))
    prof.star_template = clip.star_template(prof)
    return prof


def tune_outline(fit, yes, no):
    """The fitted outline, resized so the star COUNT works too. fit_stars lines the outline up with the strongest
    edge, and on a lit star that is where the white middle meets its dark rim: a little inside the star's real edge.
    index.py counts a star by a dark rim just inside the outline round a lighter middle, so an outline that sits on
    the middle's edge counts nothing (measured on the 26 October recordings: no star counted up to radius 15, all of
    them from 15.2 or 17.3 on). Here the size is stepped (the stars' middles stay put) and index.py's own rule
    counts the stars on the pictures that show them. If the fit's own size already counts the most, it stays.
    Otherwise the outline becomes the smallest size that counts the most, plus one step for safety, as long as
    clip.py's star score still tells the pictures with stars from those without. A game whose stars have no such
    rim changes nothing: nothing is counted at any size, and the fit stays as it is."""
    import index
    box = fit["box"]
    rule = dict(index.STAR_COUNT)
    base = {k: fit[k] for k in ("first_tip_x", "tip_spacing", "tip_y", "radius", "inner", "slots")}

    def sized(r):
        return dict(base, radius=round(r, 2), tip_y=round(fit["tip_y"] + fit["radius"] - r, 2))

    def counted(geom):
        prof = _star_prof(box, geom)
        masks = index.star_masks(prof, rule)
        return sum(index.count_stars(index.star_slots(raw, prof, masks), rule) for raw in yes)

    def fits(geom):
        r = geom["radius"]
        return (r >= cal.STAR_MIN_R and geom["tip_y"] >= box[1] - cal.STAR_TOL
                and geom["tip_y"] + r * (1 + cal.SIN54) <= box[1] + box[3] + cal.STAR_TOL
                and geom["first_tip_x"] - r * cal.COS18 >= box[0] - cal.STAR_TOL
                and geom["first_tip_x"] + geom["tip_spacing"] * (geom["slots"] - 1) + r * cal.COS18
                <= box[0] + box[2] + cal.STAR_TOL)

    steps = round((TUNE_REACH[0] + TUNE_REACH[1]) / TUNE_STEP)
    sizes = [g for g in (sized(fit["radius"] - TUNE_REACH[0] + k * TUNE_STEP) for k in range(steps + 1)) if fits(g)]
    counts = [counted(g) for g in sizes]
    own = counted(base)
    if not counts or max(counts) <= own:
        return fit
    first = counts.index(max(counts))
    pick = first + 1 if first + 1 < len(sizes) and counts[first + 1] == counts[first] else first
    best = sizes[pick]
    scores_yes, scores_no = cal.star_scores(box, best, yes), cal.star_scores(box, best, no)
    if min(scores_yes) <= max(scores_no):
        return fit
    return {**fit, **best, "lowest_yes": round(min(scores_yes), 3), "highest_no": round(max(scores_no), 3),
            "tuned_for_count": True, "separates": True}


# --- Which frames go on the page ---

def _integral(mask, w, h):
    """Prefix sums of a 0/1 mask (w x h, row by row): I[(y) * (w + 1) + x] = sum of mask[:y, :x]."""
    W = w + 1
    out = [0] * (W * (h + 1))
    for y in range(h):
        run, row, base, prev = 0, y * w, (y + 1) * W, y * W
        for x in range(w):
            run += mask[row + x]
            out[base + x + 1] = out[prev + x + 1] + run
    return out


def _box_sum(I, w, x0, y0, x1, y1):
    W = w + 1
    return I[y1 * W + x1] - I[y0 * W + x1] - I[y1 * W + x0] + I[y0 * W + x0]


def _lums(raw):
    r, g, b = raw[0::3], raw[1::3], raw[2::3]
    return [0.2126 * x + 0.7152 * y + 0.0722 * z for x, y, z in zip(r, g, b)]


def arrow_votes(raw, w, h):
    """GTA 6's player arrow in one full-size crop: every spot (x, y) of the crop with a white middle (at least
    ARROW_CORE_PX bright pixels within 3 px) inside a dark disc (ARROW_RING_SHARE of the pixels 6-7 px out dark)."""
    lum = _lums(raw)
    Ib = _integral([1 if v >= ARROW_BRIGHT else 0 for v in lum], w, h)
    Id = _integral([1 if v <= ARROW_DARK else 0 for v in lum], w, h)
    ring_px = 15 * 15 - 11 * 11
    out = []
    for y in range(8, h - 8):
        for x in range(8, w - 8):
            if _box_sum(Ib, w, x - 3, y - 3, x + 4, y + 4) < ARROW_CORE_PX:
                continue
            dark = _box_sum(Id, w, x - 7, y - 7, x + 8, y + 8) - _box_sum(Id, w, x - 5, y - 5, x + 6, y + 6)
            if dark >= ARROW_RING_SHARE * ring_px:
                out.append((x, y))
    return out


def find_arrow(frames, frame, read, log=lambda m: None):
    """GTA 6 (launch-fixes): the minimap has no coloured bars, so look for its player arrow instead: a white pointer in
    a dark disc that sits on exactly the same spot whenever the minimap is on screen. frames: [(time, small RGB)]
    (black ones are skipped); read(time, rect) a full-size area. The spot that passes in the most frames wins, if it
    passes in ARROW_MIN_SHARE of them. Returns {"centre": [x, y] (whole picture), "votes", "looked", "rect"} or None."""
    fw, fh = frame
    rx, ry, rw, rh = (round(ARROW_REGION[0] * fw), round(ARROW_REGION[1] * fh), round(ARROW_REGION[2] * fw),
                      round(ARROW_REGION[3] * fh))
    rect = [rx, ry, rw, rh]
    looks = [t for t, raw in spread([fr for fr in frames if not is_black(fr[1])], ARROW_LOOK)]
    if not looks:
        return None
    with ThreadPoolExecutor(SCAN_WORKERS) as pool:
        crops = list(pool.map(lambda t: read(t, rect), looks))
    votes = {}
    looked = 0
    for raw in crops:
        if not raw:
            continue
        looked += 1
        seen = set()
        for x, y in arrow_votes(raw, rw, rh):
            key = (x // 2, y // 2)              # 2 px cells: the pointer's turn moves the best spot a little
            if key not in seen:
                seen.add(key)
                votes[key] = votes.get(key, 0) + 1
    if not votes or not looked:
        return None
    best = max(votes, key=votes.get)
    if votes[best] < max(3, ARROW_MIN_SHARE * looked):
        log(f"  Minimap arrow: the best spot passed in only {votes[best]} of {looked} looks (needs "
            f"{max(3, math.ceil(ARROW_MIN_SHARE * looked))}): not found.")
        return None
    near = {k: v for k, v in votes.items() if abs(k[0] - best[0]) <= 1 and abs(k[1] - best[1]) <= 1}
    total = sum(near.values())
    cx = rx + sum((k[0] * 2 + 1) * v for k, v in near.items()) / total + 0.5
    cy = ry + sum((k[1] * 2 + 1) * v for k, v in near.items()) / total + 0.5
    # The vote cells are 2 px and the pointer turns, so the centre is fine-tuned: the spot within 3 px where the
    # disc's middle is brightest and its ring darkest, over the looks that show the arrow there.
    showing = [raw for raw in crops if raw and arrow_guess(raw, rect, (cx, cy), loose=True)]
    if showing:
        cx, cy = max(((cx + dx / 2, cy + dy / 2) for dx in range(-6, 7) for dy in range(-6, 7)),
                     key=lambda c: sum(arrow_fit(raw, rect, c) for raw in showing))
    return {"centre": [round(cx, 1), round(cy, 1)], "votes": votes[best], "looked": looked, "rect": rect}


def arrow_shares(raw, rect, centre):
    """(share of bright pixels in the arrow's middle, share of dark pixels in the ring inside its disc's edge) on one
    full-size crop, with clip.py's default radii and brightness limits."""
    a = clip.ARROW_DEFAULTS
    x0, y0, w, h = rect
    bright = dark = nb = nd = 0
    for y in range(max(0, int(centre[1] - y0 - 10)), min(h, int(centre[1] - y0 + 11))):
        for x in range(max(0, int(centre[0] - x0 - 10)), min(w, int(centre[0] - x0 + 11))):
            d = math.hypot(x0 + x + 0.5 - centre[0], y0 + y + 0.5 - centre[1])
            i = (y * w + x) * 3
            v = 0.2126 * raw[i] + 0.7152 * raw[i + 1] + 0.0722 * raw[i + 2]
            if d <= a["core_r"]:
                nb += 1
                bright += v >= a["core_min"]
            elif a["ring"][0] <= d <= a["ring"][1]:
                nd += 1
                dark += v <= a["ring_max"]
    return (bright / nb if nb else 0.0), (dark / nd if nd else 0.0)


def arrow_fit(raw, rect, centre):
    bright, dark = arrow_shares(raw, rect, centre)
    return bright + dark


def arrow_guess(raw, rect, centre, loose=False):
    """HUD shown (True) or hidden (False) on one full-size crop, from the arrow at centre with clip.py's defaults
    (loose: half the limits, while the centre is still being fine-tuned)."""
    a = clip.ARROW_DEFAULTS
    bright, dark = arrow_shares(raw, rect, centre)
    k = 0.5 if loose else 1.0
    return bright >= k * a["core_share"] and dark >= k * a["ring_share"]


def red_blobs(mask, w, h):
    """Connected groups (4-neighbours) of set pixels: [(x0, y0, x1, y1, pixels)]."""
    seen, out = bytearray(w * h), []
    for start in range(w * h):
        if not mask[start] or seen[start]:
            continue
        stack, px = [start], []
        seen[start] = 1
        while stack:
            i = stack.pop()
            px.append(i)
            x, y = i % w, i // w
            for j in ((i - 1) if x else -1, (i + 1) if x < w - 1 else -1, i - w, i + w):
                if 0 <= j < w * h and mask[j] and not seen[j]:
                    seen[j] = 1
                    stack.append(j)
        xs, ys = [i % w for i in px], [i // w for i in px]
        out.append((min(xs), min(ys), max(xs) + 1, max(ys) + 1, len(px)))
    return out


def find_gta6_stars(shown, hidden, frame, read, slots=None, log=lambda m: None):
    """GTA 6 (launch-fixes): the wanted-star row from lit red stars. shown/hidden: times of frames with the HUD on
    screen or not. Red pixels that sit on the same spot in some (not all) HUD frames, in star-sized groups at the top
    right, are lit stars; the row is fitted (calibrate.fit_stars, GTA 6 style) from frames with them against frames
    without any (the money's green wallet sits in the row then, or the HUD is gone). Returns find_stars' shape, or
    None when no recording frame shows red stars (white ones are left to the page: draw the box round them)."""
    fw, fh = frame
    rect = [round(STAR6_REGION[0] * fw), round(STAR6_REGION[1] * fh), round(STAR6_REGION[2] * fw),
            round(STAR6_REGION[3] * fh)]
    rx, ry, rw, rh = rect
    looks = spread(list(shown), ARROW_LOOK)
    with ThreadPoolExecutor(SCAN_WORKERS) as pool:
        crops = {t: r for t, r in zip(looks, pool.map(lambda t: read(t, rect), looks)) if r}
    if len(crops) < 3:
        return None

    def reds(raw):
        return bytearray(1 if raw[i] >= 110 and raw[i] - max(raw[i + 1], raw[i + 2]) >= 80 else 0
                         for i in range(0, len(raw), 3))

    def greens(raw, y0, y1):
        """Green pixels in rows y0..y1 of a crop: the money's wallet icon, which sits IN the star row only when no
        star is lit (while wanted the money moves below the stars)."""
        return sum(1 for i in range(y0 * rw * 3, min(rh, y1) * rw * 3, 3)
                   if raw[i + 1] >= 90 and raw[i + 1] - max(raw[i], raw[i + 2]) >= 40)

    masks = {t: reds(r) for t, r in crops.items()}
    count = [0] * (rw * rh)
    for m in masks.values():
        for i, v in enumerate(m):
            count[i] += v
    steady = bytearray(1 if 2 <= c <= 0.9 * len(masks) else 0 for c in count)
    blobs = [b for b in red_blobs(steady, rw, rh) if b[4] >= STAR6_MIN_PX and 0.5 <= (b[2] - b[0]) / max(1, b[3] - b[1]) <= 2.0]
    if not blobs:
        log("  Wanted stars: no red stars seen in this recording.")
        return None
    # The heat icons under the stars are red too: the stars are the top row of red shapes.
    top = min(blobs, key=lambda b: (b[1] + b[3]) / 2)
    mid, tall = (top[1] + top[3]) / 2, top[3] - top[1]
    blobs = [b for b in blobs if abs((b[1] + b[3]) / 2 - mid) <= 0.5 * tall]
    blobs.sort(key=lambda b: b[0])
    size = max(max(b[2] - b[0], b[3] - b[1]) for b in blobs)
    gaps = [b2[0] - b1[0] for b1, b2 in zip(blobs, blobs[1:]) if b2[0] - b1[0] > size]
    spacing = min(gaps) if gaps else round(size * 2.1)
    n = slots or max(len(blobs), 1)
    # Room round the row (about a third of a star's width each side, and above and below), as fit_stars needs: with
    # a tight box its search settles on smaller stars inside the real ones.
    pad = max(10, round(0.3 * size))
    x1 = min(fw, rx + max(b[2] for b in blobs) + 2 * pad)
    x0 = max(0, rx + max(b[2] for b in blobs) - n * spacing - pad)
    y0 = max(0, ry + min(b[1] for b in blobs) - pad)
    y1 = min(fh, ry + max(b[3] for b in blobs) + pad)
    box = [x0, y0, x1 - x0, y1 - y0]
    lit = [(y - ry) * rw + (x - rx) for b in blobs for y in range(ry + b[1], ry + b[3]) for x in range(rx + b[0], rx + b[2])
           if steady[(y - ry) * rw + (x - rx)]]
    yes = [t for t, m in masks.items() if sum(m[i] for i in lit) >= 0.5 * len(lit)]
    row0, row1 = min(b[1] for b in blobs), max(b[3] for b in blobs)
    no = [t for t, r in crops.items() if t not in yes and sum(masks[t][i] for i in lit) == 0
          and greens(r, row0, row1) >= 20]
    no += list(spread(list(hidden), max(0, 4 - len(no))))
    if len(yes) < cal.STAR_MIN_YES or len(no) < cal.STAR_MIN_NO:
        log(f"  Wanted stars: red stars on {len(yes)} looks, {len(no)} looks surely without: too few to fit the row.")
        return None
    with ThreadPoolExecutor(SCAN_WORKERS) as pool:
        yes_raws = [r for r in pool.map(lambda t: read(t, box), yes) if r]
        no_raws = [r for r in pool.map(lambda t: read(t, box), no) if r]
    try:
        fit = cal.fit_stars(box, yes_raws, no_raws, slots, "gta6")
    except cal.CalibError as e:
        log(f"  Wanted stars: red stars seen, but the row could not be fitted ({e}).")
        return None
    fit["box"] = row_rect(fit, frame)
    return {"fit": fit, "yes": sorted(yes), "no": sorted(no), "alike": {t: 1.0 for t in yes}}


def analyse(frames, w, h, frame, read, slots=None, log=lambda m: None):
    """Everything the scan can tell. frames: [(time, small RGB)]; frame: the full picture's size; read(time, rect):
    a full-size area. Returns {"bars": coarse_bars or None, "arrow": find_arrow or None (GTA 6, when there are no
    bars), "stars": find_stars or None, "frames": [{"t", "black", "hud": True/False/None, "stars": True/False/None
    (None: can't tell)}]}."""
    raws = [r for _, r in frames]
    info = [{"t": t, "black": is_black(r), "hud": None, "stars": None} for t, r in frames]
    stars = None
    # GTA V's coloured bars first, as before. They sit by the minimap, low on the picture: "bars" anywhere else are
    # something else (GTA 6's red compass bar at the top while wanted passed as bars on launch night, _014828).
    # With no bars there, GTA 6's player arrow is looked for instead.
    bars = coarse_bars(raws, w, h)
    if bars and full_rect(bars["box"])[1] < BARS_MIN_Y * frame[1]:
        log(f"  Coloured strips found at {full_rect(bars['box'])}, high on the picture: not minimap bars.")
        bars = None
    arrow = None if bars else find_arrow(frames, frame, read, log)
    if not bars:
        if arrow:
            rect = arrow["rect"]
            with ThreadPoolExecutor(SCAN_WORKERS) as pool:
                crops = list(pool.map(lambda f: None if f["black"] else read(f["t"], rect), info))
            for f, raw in zip(info, crops):
                f["hud"] = bool(raw) and arrow_guess(raw, rect, arrow["centre"])
            stars = find_gta6_stars([f["t"] for f in info if f["hud"]], [f["t"] for f in info if f["hud"] is False],
                                    frame, read, slots, log)
            if stars:
                # Every HUD frame gets a guess from the fitted row itself: lit stars counted the GTA 6 way.
                fit, box = stars["fit"], stars["fit"]["box"]
                hud = [f for f in info if f["hud"]]
                with ThreadPoolExecutor(SCAN_WORKERS) as pool:
                    crops = list(pool.map(lambda f: read(f["t"], box), hud))
                for f, raw in zip(hud, crops):
                    if raw:
                        n = cal.star_scores(box, fit, [raw], "gta6")[0]
                        f["stars"], f["alike"] = n >= 1, float(n)
                stars["yes"] = sorted(f["t"] for f in hud if f["stars"])
                stars["no"] = sorted(f["t"] for f in hud if f["stars"] is False)
        return {"bars": None, "arrow": arrow, "stars": stars, "frames": info}
    if bars:
        for f, r in zip(info, raws):
            f["hud"] = (not f["black"]) and hud_shown(r, w, bars)
        stars = find_stars([fr for fr, f in zip(frames, info) if f["hud"]], w, h, frame, read, slots, log)
        if stars:
            yes, no = set(stars["yes"]), set(stars["no"])
            for f in info:
                if f["hud"]:
                    f["stars"] = True if f["t"] in yes else False if f["t"] in no else None
                    f["alike"] = stars["alike"].get(f["t"], 0.0)
    return {"bars": bars, "stars": stars, "frames": info}


def choose_frames(found, shown=6, hidden=3, stars=4):
    """The scan frames that go on the page, as {"t", "hud", "stars"} guesses ("shown"/"hidden"/None and "yes"/"no"):
    frames with stars (the surest ones, spread over the recording), HUD-shown frames surely without stars, and
    frames with the HUD hidden. Without bars there are no guesses: an even spread of frames that aren't black."""
    fr = found["frames"]
    if not (found["bars"] or found.get("arrow")):
        return [{"t": f["t"], "hud": None, "stars": "no"}
                for f in spread([f for f in fr if not f["black"]], shown + hidden + stars)]
    yes = sorted(sorted((f for f in fr if f["stars"]), key=lambda f: -f["alike"])[:stars * 3], key=lambda f: f["t"])
    no = [f for f in fr if f["hud"] and (f["stars"] is False or not found["stars"])]
    gone = [f for f in fr if not f["hud"] and not f["black"]]
    black = [f for f in fr if f["black"]]
    picks = ([{"t": f["t"], "hud": "shown", "stars": "yes"} for f in spread(yes, stars)]
             + [{"t": f["t"], "hud": "shown", "stars": "no"} for f in spread(no, shown)]
             + [{"t": f["t"], "hud": "hidden", "stars": "no"} for f in spread(gone, hidden)])
    if black and len(gone) < hidden:
        picks.append({"t": black[len(black) // 2]["t"], "hud": "hidden", "stars": "no"})
    return sorted(picks, key=lambda p: p["t"])


def check_window(found, duration, length=180.0):
    """(start, length) of the part the finished profile is tried on: round the frames with stars when there are
    some, else where the HUD is on screen the most."""
    length = min(length, duration)
    fr = found["frames"]
    marks = [f["t"] for f in fr if f["stars"]] or [f["t"] for f in fr if f["hud"]]
    if not marks:
        return 0.0, length
    best = max((sum(1 for m in marks if s <= m < s + length), -s, s)
               for s in sorted({max(0.0, min(duration - length, m - 15.0)) for m in marks} | {0.0}))
    return round(best[2], 1), length


# --- The guided command itself: calibrate.py --guided --profile NAME --video RECORDING ---

CHECK_S = 180.0          # the saved profile is tried on this much of the recording
STAR_CHECK_EACH = 3      # pictures per star count on the star check page


def guess_game(name):
    """The game's name for messages, from the profile's name."""
    return "GTA 6" if "gta6" in name or "gtavi" in name else "GTA V" if "gta5" in name else name


def pending_parts(name, profile_dir=None):
    """What a saved profile still has to do: None when there is no profile, [] when it is finished, else the parts
    guided mode marked (["stars"], ["phone"] or both). Only "stars" lets the profile be written again."""
    path = Path(profile_dir or clip.PROFILE_DIR) / f"{name}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return list(((data.get("calibration") or {}).get("guided") or {}).get("pending") or [])


def fill_session(session, video, found, picks, slots=None, game=None, log=print):
    """Put what the scan found into the calibration session, as guesses the page shows: the frames, the bars (found
    properly at full size by Session.find_bars inside the scan's rough box), a guess for every label, the star row,
    and the phone switched off for now. Nothing is confirmed here: the owner does that on the page. Returns the
    session's "guided" block."""
    d = session.data
    before = {f["id"] for f in d["frames"]}
    session.add(video, [f"{p['t']:.2f}" for p in picks])
    new = [f for f in d["frames"] if f["id"] not in before]
    guess = {f["id"]: next(p for p in picks if abs(p["t"] - f["time_s"]) < 0.02) for f in new}
    drawing = d["drawing"]
    bars_ok = False
    shown = [f["id"] for f in d["frames"]
             if f["labels"].get("hud") == "shown" or (f["id"] in guess and guess[f["id"]]["hud"] == "shown")]
    rect = (drawing.get("bar_area") if drawing.get("bars") and drawing.get("bar_area")
            else full_rect(found["bars"]["box"], BAR_PAD) if found["bars"] else None)
    arrow = found.get("arrow")
    if arrow and not (drawing.get("arrow") or {}).get("confirmed"):
        drawing["arrow"] = {"centre": list(arrow["centre"]), "auto": True, "confirmed": False,
                            "votes": arrow["votes"], "looked": arrow["looked"]}
        log(f"  Minimap arrow: found at x {arrow['centre'][0]:g}, y {arrow['centre'][1]:g} "
            f"(on {arrow['votes']} of {arrow['looked']} looks).")
        rect = None
    if rect:
        try:
            log("  " + session.find_bars(rect, use=shown))
            bars_ok = True
        except cal.CalibError as e:
            log(f"  The bars were not found at full size ({e}). You will drag a box round them on the page.")
    for f in new:
        suggest = f.setdefault("suggest", {})
        if suggest.get("hud") is None and guess[f["id"]]["hud"]:
            suggest["hud"], suggest["why"] = guess[f["id"]]["hud"], "the scan's guess"
        suggest["stars"], suggest["phone"] = guess[f["id"]]["stars"] or "no", "no"
    stars = drawing.setdefault("stars", {})
    done_before = bool((stars.get("fit") or {}).get("confirmed")) or stars.get("mode") == "clicks"
    if found["stars"]:
        stars.update(box=list(found["stars"]["fit"]["box"]), fit=dict(found["stars"]["fit"]), mode="fit", off=False)
        if found["stars"]["fit"].get("style") == "gta6":
            stars["style"] = "gta6"
        star_state = "found"
    elif done_before and not stars.get("off"):
        star_state = "kept"
    else:
        stars["off"] = True
        star_state = "none"
    phone = drawing.setdefault("phone", {})
    phone.setdefault("grey", [])
    drawn = bool(phone.get("box") and phone.get("dark") and phone.get("bright") and phone["grey"])
    if not drawn:
        phone["off"] = True
    d["game"] = d.get("game") or game or guess_game(session.name)
    d["guided"] = {"video": str(Path(video).resolve()), "video_name": Path(video).name, "bars": bars_ok,
                   "arrow": bool(drawing.get("arrow")),
                   "stars": star_state, "phone": "drawn" if drawn else "skipped", "slots_told": slots,
                   "frames_added": len(new), "started": cal.now()}
    session.event("guided_fill", bars=bars_ok, stars=star_state, frames=len(new))
    session.save()
    return d["guided"]


def cut_excerpt(ffmpeg, ffprobe, video, info, start, length, out_dir):
    """A copy (no re-encode) of length seconds of the recording from start, for the check. Returns (path, None), or
    (None, why) when the copy can't be made or doesn't read back as the same kind of picture: a PS5 .webm is stored
    1920x1088 with a container crop, and a copy that lost it would shift every HUD box."""
    out = Path(out_dir) / f"{Path(video).stem}_check{Path(video).suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)
    done = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
                           "-i", str(video), "-map", "0:v:0", "-map", "0:a?", "-c", "copy", str(out)],
                          capture_output=True, text=True)
    if done.returncode or not out.is_file():
        return None, "ffmpeg could not copy that part: " + done.stderr.strip()[-200:]
    try:
        got = clip.probe(ffprobe, out)
    except SystemExit:
        return None, "the copy can't be read back"
    keys = ("width", "height", "container_crop", "video_codec", "color_range", "pix_fmt")
    if any(got[k] != info[k] for k in keys):
        return None, "the copy doesn't read back as the same picture (" + ", ".join(
            f"{k} {info[k]} became {got[k]}" for k in keys if got[k] != info[k]) + ")"
    return out, None


def verdict(index_run, stars_on, stars_expected, star_pictures, measure_problems, pending):
    """(result, problems) from what the checks found. index_run: the index dict, None when the index didn't run, or
    "skipped". FAIL if anything is wrong; a profile whose stars are still to do is never plain PASS."""
    problems = list(measure_problems)
    if index_run is None:
        problems.append("the 3-minute index did not finish, so the profile wasn't tried on moving footage")
    elif index_run != "skipped":
        hud = index_run["hud"]
        if not hud["on"]:
            problems.append(f"the minimap was found in only {hud['minimap_share']:.0%} of the 3 minutes: the profile's "
                            "bars don't match this recording")
        if stars_on and stars_expected and hud["stars_s"] <= 0:
            problems.append("the scan saw wanted stars in these 3 minutes, but the saved profile finds none there")
        if stars_on and hud["stars_s"] > 0 and star_pictures == 0:
            problems.append("the star check page has no pictures, though the index found stars")
    if problems:
        return "FAIL", problems
    if index_run == "skipped":
        return "SAVED, NOT CHECKED (--skip-index)" + (". STARS NOT CALIBRATED" if "stars" in pending else ""), problems
    if "stars" in pending:
        return "PASS for the minimap. STARS NOT CALIBRATED", problems
    return "PASS", problems


def check_profile(name, video, info, window, stars_expected, out_dir, ffmpeg, ffprobe, skip_index=False, log=print,
                  index_video=None, star_check=None):
    """Try the saved profile: index CHECK_S of the recording with it (index.py) and build the star check page
    (star_check.py), in a library of their own under out_dir so the real library is never touched. Returns a dict
    with the result, the problems and the pages. index_video and star_check are swapped by the tests."""
    import index
    prof = clip.load_profile(name)
    saved = json.loads((clip.PROFILE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    calib = saved.get("calibration") or {}
    pending = (calib.get("guided") or {}).get("pending") or []
    measure_problems = [f"{rule}: saved although some labelled pictures come out wrong" for rule, status
                        in (calib.get("status") or {}).items() if status == "problem"]
    out = {"profile": str(clip.PROFILE_DIR / f"{name}.json"), "stars_on": prof.stars_on, "phone_on": prof.phone_on,
           "pending": pending, "review": None, "star_check": None, "index": None, "star_pictures": 0}
    index_run = "skipped"
    if not skip_index:
        library = Path(out_dir) / "library"
        start, length = window
        target, limit = None, None
        clip_file, why = cut_excerpt(ffmpeg, ffprobe, video, info, start, length, Path(out_dir))
        if clip_file:
            target = clip_file
            log(f"  Checking {clip.hms(start)} to {clip.hms(start + length)} of the recording"
                + (" (the part where the scan saw wanted stars)." if stars_expected else "."))
        else:
            target, limit, stars_expected = Path(video), length, False
            log(f"  Checking the first {length:.0f} s of the recording ({why}).")
        try:
            index_run = (index_video or index.index_video)(target, profile=name, library=library, limit_s=limit,
                                                           log=lambda m: log("    " + m))
            out["review"] = str(library / target.stem / "review.html")
            out["index"] = {"minimap_share": index_run["hud"]["minimap_share"], "hud_on": index_run["hud"]["on"],
                            "stars_s": index_run["hud"]["stars_s"], "segments": len(index_run["segments"]),
                            "max_stars": max((s["max_stars"] for s in index_run["segments"]), default=0)}
        except index.IndexError_ as e:
            index_run = None
            log(f"  The index stopped: {e}")
        if index_run and prof.stars_on and index_run["hud"]["stars_s"] > 0:
            counts = sorted({s["hud_stars"] for s in index_run["sheets"] if s.get("hud_stars")})
            out["star_pictures"] = (star_check or run_star_check)(target.stem, counts, library, log)
            out["star_check"] = str(library / "star_check.html") if out["star_pictures"] else None
    out["result"], out["problems"] = verdict(index_run, prof.stars_on, stars_expected, out["star_pictures"],
                                             measure_problems, pending)
    return out


def run_star_check(stem, counts, library, log=print):
    """star_check.py on one recording of the check library; returns how many pictures its page shows."""
    here = Path(__file__).resolve().parent
    done = subprocess.run([sys.executable, str(here / "star_check.py"), stem, "--want",
                           *[f"{c}:{STAR_CHECK_EACH}" for c in counts], "--library", str(library)],
                          capture_output=True, text=True, cwd=here)
    for line in done.stdout.strip().splitlines():
        log("    " + line)
    if done.returncode:
        log("    star_check.py stopped: " + done.stderr.strip()[-300:])
        return 0
    found = re.search(r"\((\d+) pictures in", done.stdout)
    return int(found.group(1)) if found else 0


def auto_accept(session, port=0, browser=False, guided=True):
    """The page's part done with no person (launch.py): every guess the scan made is taken as it is, the star
    outline it fitted is confirmed, and the profile is saved even where a rule measures one-sided ("Save anyway").
    Returns False, with nothing saved, when the profile can't be saved at all (no bars found, no game name): the
    caller then runs without HUD rules. Use it as run(..., serve=auto_accept)."""
    session.event("page_opened")
    if not session.data.get("game"):
        session.data["game"] = guess_game(session.name)
    for f in session.data["frames"]:
        for k in ("hud", "stars", "phone"):
            if f["labels"].get(k) is None and (f.get("suggest") or {}).get(k) is not None:
                f["labels"][k] = f["suggest"][k]
    fit = session.data["drawing"]["stars"].get("fit")
    if fit and not session.data["drawing"]["stars"].get("off"):
        fit["confirmed"] = True
    arrow = session.data["drawing"].get("arrow")
    if arrow and arrow.get("centre"):
        arrow["confirmed"] = True
    try:
        session.save_profile(True)
    except (cal.CalibError, ValueError, KeyError) as e:
        session.event("auto_accept_failed", why=str(e))
        session.save()
        return False
    return True


def scan_one(session, video, slots, log):
    """Scan and analyse one recording. Returns (found, info, a one-line summary, a score: how good a base it is)."""
    info = clip.probe(session.ffprobe, video)
    log(f"  {video.name} ({clip.hms(info['duration'])}): looking through it...")
    frames = scan(session.ffmpeg, video, info)
    w, h = small_size(info)
    found = analyse(frames, w, h, cal.shown_size(info),
                    lambda t, rect: grab_area(session.ffmpeg, video, info, t, rect), slots, lambda m: log("  " + m))
    fr = found["frames"]
    hud = sum(1 for f in fr if f["hud"])
    what = ("bars found" if found["bars"] else
            f"no bars; minimap arrow found at {found['arrow']['centre']}" if found.get("arrow") else
            "no bars and no minimap arrow")
    stars = f"stars on {len(found['stars']['yes'])} looks" if found["stars"] else "no stars found"
    summary = (f"{video.name}: {len(frames)} looks, HUD on {hud}, black {sum(1 for f in fr if f['black'])}; "
               f"{what}; {stars}")
    score = (0 if not (found["bars"] or found.get("arrow")) else 1000 + (500 if found["stars"] else 0) + hud)
    return found, info, summary, score


def run(name, video, slots=None, game=None, port=0, browser=True, skip_index=False, log=print, serve=None):
    """The guided calibration, start to finish. video: one recording or a list of them: each is scanned, every step
    is logged, and the one that shows the HUD best (stars found first) is used. Returns the exit code: 0 PASS (also
    with stars still to do), 1 FAIL or nothing saved, 2 it could not start."""
    import index
    import vlm
    t0 = time.time()
    videos = [Path(v) for v in (video if isinstance(video, (list, tuple)) else [video])]

    def stop(msg):
        log("ERROR: " + msg)
        return 2

    if not cal.NAME_RE.match(name or ""):
        return stop("the profile name must be 1-40 lowercase letters, digits or _ (it becomes profiles/NAME.json)")
    if slots is not None and not 1 <= slots <= 10:
        return stop("--star-slots is the number of wanted-star slots, 1 to 10")
    if not videos:
        return stop("no recording given")
    for v in videos:
        if not v.is_file():
            return stop(f"video not found: {v}")
        barred = index.off_limits(v)
        if barred:
            return stop(f"{v} is in {barred}: held-out footage is never used (the owner's rule)")
    pending = pending_parts(name)
    if pending is not None and "stars" not in pending:
        return stop(f"profiles/{name}.json already exists and its stars are done. calibrate.py never overwrites a "
                    f"finished profile: pick another name, for example {name}2")
    if not skip_index:
        try:
            vlm.check_ollama(index.MODEL)
        except vlm.VlmError as e:
            why = "is not running" if "not reachable" in str(e) else f"does not have the model {index.MODEL}"
            return stop(f"Ollama {why}. The check at the end needs it. Open the Ollama app (or run: ollama serve), "
                        "then run the same command again. To calibrate now and check later, add --skip-index.")
    try:
        session = cal.Session(name)
    except cal.CalibError as e:
        return stop(str(e))
    guided = session.data.get("guided") or {}
    found_hud = bool(guided.get("bars") or guided.get("arrow"))
    # Carry on only with a session whose scan found the HUD: a scan that found nothing is done again (on launch night
    # the second /launch reused a scan that had found no bars, and failed the same way).
    resume = (pending is None and bool(session.data["frames"]) and found_hud
              and guided.get("video") in {str(v.resolve()) for v in videos})
    if pending is None and session.data["frames"] and not resume:
        why = "the last scan found no HUD" if not found_hud else "the last scan used other recordings"
        log(f"Starting the scan again: {why}.")
        session.data.update(frames=[], drawing={"stars": {}, "bars": [], "phone": {"grey": []}}, overrides={})
        session.data.pop("guided", None)
        session.event("rescan", why=why)
        session.save()
    if pending and not session.data["frames"]:
        return stop(f"profiles/{name}.json is waiting for its stars, but its session folder {session.dir} is gone, so "
                    "it can't be finished. Calibrate again under another name")
    video = Path(guided["video"]) if resume else videos[0]
    info = clip.probe(session.ffprobe, video)
    try:
        if resume:
            log(f"Carrying on with the session from before ({len(session.data['frames'])} pictures; nothing was saved "
                "last time).")
        else:
            log(f"Step 1 of 5. Looking through {len(videos)} recording(s); about half a minute each...")
            tries = []
            for v in videos:
                found, inf, summary, score = scan_one(session, v, slots, log)
                log("  " + summary)
                session.event("scan", video=v.name, summary=summary, score=score)
                tries.append((score, -len(tries), v, found, inf))
            score, _, video, found, info = max(tries, key=lambda t: (t[0], t[1]))
            session.data["scans"] = [{"video": t[2].name, "score": t[0]} for t in tries]
            if score <= 0:
                log("  No recording showed a minimap the tool knows (GTA V's coloured bars or GTA 6's player arrow).")
            else:
                log(f"  Using {video.name} for the page.")
            fr = found["frames"]
            log(f"  {len(fr)} looks at {video.name}: HUD on screen in {sum(1 for f in fr if f['hud'])}, "
                f"black screen in {sum(1 for f in fr if f['black'])}.")
            if found.get("arrow"):
                log(f"  Minimap: no bars (GTA 6); its player arrow is at x {found['arrow']['centre'][0]:g}, "
                    f"y {found['arrow']['centre'][1]:g}. You check it on the page.")
            else:
                log("  Minimap bars: " + (f"found near {full_rect(found['bars']['box'])} "
                                          f"({len(found['bars']['strips'])} coloured strips)." if found["bars"] else
                                          "NOT found. You will drag one box round them on the page."))
            if found["stars"]:
                fit = found["stars"]["fit"]
                log(f"  Wanted stars: found, {fit['slots']} slot(s) at {fit['box']}, on screen in "
                    f"{len(found['stars']['yes'])} of the looks.")
            else:
                log("  Wanted stars: none found in this recording.")
            log("Step 2 of 5. Grabbing the pictures for the page...")
            picks = choose_frames(found) if not session.data["frames"] else choose_frames(found, shown=3, hidden=1)
            if pending:
                session.replace_ok = True
                session.data.pop("saved", None)
            guided = fill_session(session, video, found, picks, slots, game, log)
            guided["check_window"] = list(check_window(found, info["duration"], CHECK_S))
            guided["stars_in_window"] = bool(found["stars"]) and any(
                guided["check_window"][0] <= t < sum(guided["check_window"]) for t in found["stars"]["yes"])
            guided["scan_s"] = round(time.time() - t0, 1)
            guided["recordings_scanned"] = [v.name for v in videos]
            session.save()
    except cal.CalibError as e:
        return stop(str(e))
    if pending:
        session.replace_ok = True
    log("Step 3 of 5. YOUR TURN. A page opens in your browser. The blue box at its top lists what to check.")
    log("  When you press Save there, come back here: the checks start by themselves.")
    t_page = time.time()
    if not (serve or cal.serve)(session, port, browser, guided=True):
        log("Nothing was saved. Run the same command again to carry on where you stopped.")
        return 1
    t_saved = time.time()
    log("Step 4 of 5. Trying the new profile on 3 minutes of the recording"
        + (" (skipped: --skip-index)." if skip_index else f" (index.py with {index.MODEL}; about 2 minutes)..."))
    window = guided.get("check_window") or [0.0, min(CHECK_S, info["duration"])]
    result = check_profile(name, video, info, window, bool(guided.get("stars_in_window")), session.dir / "check",
                           session.ffmpeg, session.ffprobe, skip_index, log)
    t_done = time.time()
    result["minutes"] = {"scan": round(guided.get("scan_s", 0) / 60, 1), "page": round((t_saved - t_page) / 60, 1),
                         "checks": round((t_done - t_saved) / 60, 1),
                         "start_to_save": round((guided.get("scan_s", 0) + t_saved - t_page) / 60, 1),
                         "total": round((guided.get("scan_s", 0) + t_done - t_page) / 60, 1)}
    (session.dir / "guided_result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    log("Step 5 of 5. Result.")
    for line in result_lines(name, video, result):
        log(line)
    return 1 if result["result"] == "FAIL" else 0


def result_lines(name, video, r):
    """The last thing the guided command prints: the result, what to look at, and how long it took."""
    idx, m = r["index"], r["minutes"]
    lines = ["", f"RESULT: {r['result']}"]
    lines += [f"  Problem: {p}" for p in r["problems"]]
    lines.append(f"  Profile:      {r['profile']}")
    if idx:
        lines.append(f"  Minimap:      found in {idx['minimap_share']:.0%} of the 3 minutes checked")
    if "stars" in r["pending"]:
        lines.append("  Wanted stars: NOT CALIBRATED. This recording showed none. Record a clip where you get wanted "
                     "stars, then run:")
        lines.append(f"                  .\\.venv\\Scripts\\python.exe calibrate.py --guided --profile {name} "
                     "--video \"<that clip>\" --star-slots 6")
    elif r["stars_on"]:
        lines.append("  Wanted stars: calibrated" + (f"; showing for {idx['stars_s']:.0f} s of the 3 minutes, up to "
                                                      f"{idx['max_stars']} star(s) counted" if idx else ""))
    else:
        lines.append("  Wanted stars: switched off by you")
    lines.append("  Phone calls:  " + ("calibrated" if r["phone_on"] else "not calibrated yet (skipped; phone calls "
                                                                           "won't be cut out)"))
    if r["review"]:
        lines.append(f"  Look at:      {r['review']}")
        lines.append("                  (one row per segment: red rows are cutscenes or menus; do the labels and stars fit?)")
    if r["star_check"]:
        lines.append(f"                {r['star_check']}")
        lines.append(f"                  ({r['star_pictures']} pictures: is the star count under each one right?)")
    lines.append(f"  Time:         {m['start_to_save']} min from the command to Save (scan {m['scan']}, your part "
                 f"{m['page']}), checks {m['checks']}; {m['total']} min in all")
    return lines
