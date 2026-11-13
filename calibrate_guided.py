"""The automatic half of `calibrate.py --guided` (PR 22, session 2): look through a recording without a profile and
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
import math
import subprocess
from concurrent.futures import ThreadPoolExecutor

import calibrate as cal

SCAN_FRAMES = 120        # looks at the recording (fewer for a short one: never closer than SCAN_MIN_GAP_S)
SCAN_MIN_GAP_S = 2.0
SCAN_WORKERS = 6
DIV = 4                  # a small frame is the picture at 1/DIV size
BLACK_MEAN = 14.0        # a small frame whose average channel value is under this is a black screen

# coarse_bars. Judgement values, set on the GTA V recordings and on calibguidedtest.py's made-up HUDs.
BARS_USE = 40            # at most this many frames (spread evenly) go into the search
HUE_BUCKETS = 12         # 30 degrees each
BAR_SHARE = 0.25         # a bar pixel has one hue on at least this share of the frames (the HUD may be off on the rest)
BAR_MIN_RUN = 5          # small pixels: a bar is at least this long (20 px of the real picture)
BAR_THICK = 4            # small pixels: 4 rows up and 4 rows down the hue is gone, or the strip is no bar
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
    need = max(3, math.ceil(BAR_SHARE * len(images)))
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
    if len(images) < 3:
        return None
    px = bar_pixels(images, w, h)

    def same(x, y, bucket):
        v = px[y * w + x] if 0 <= y < h else None
        return v is not None and _near(v[0], bucket)

    runs = []   # (y, x0, x1, bucket): a level run of one hue that is gone BAR_THICK rows above and below
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
            above = sum(same(i, y - BAR_THICK, bucket) for i in range(x0, x))
            below = sum(same(i, y + BAR_THICK, bucket) for i in range(x0, x))
            if max(above, below) <= 0.3 * (x - x0):
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
        row = sorted((t for t in strips if t["y"][0] < s["y"][1] + 1 and s["y"][0] < t["y"][1] + 1),
                     key=lambda t: t["x"][0])
        row = [t for i, t in enumerate(row) if i == 0 or t["x"][0] >= row[i - 1]["x"][1]]   # side by side only
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
        # The owner said how many slots the row has. Every slot must fit in the box (stars are about as wide as
        # they are tall), so the row's box is widened on both sides and the fit is told the number.
        want = round(slots * 1.25 * (rect[3] - 2 * STAR_PAD[1])) + 2 * STAR_PAD[0]
        x0 = max(0, rect[0] - max(0, want - rect[2]))
        wide = [x0, rect[1], min(frame[0], rect[0] + rect[2] + max(0, want - rect[2])) - x0, rect[3]]
        try:
            told = _fit(read, on, yes_i, no_i, wide, slots)
            if told and told["separates"]:
                tight = row_rect(told, frame)
                again = _fit(read, on, yes_i, no_i, tight, slots) if tight != wide else None
                fit = again if again and again["separates"] else told
                log(f"    with {slots} slots, as you said: the row is at {fit['box']}")
            else:
                log(f"    {slots} slots, as you said, don't fit what the pictures show; keeping the {fit['slots']} found")
        except cal.CalibError:
            log(f"    {slots} slots, as you said, don't fit in the row's box; keeping the {fit['slots']} found")
    return {"fit": fit, "alike": {t: round(a, 3) for (t, _), a in zip(on, alike)},
            "yes": [t for (t, _), a in zip(on, alike) if a >= STAR_ALIKE],
            "no": [t for (t, _), a in zip(on, alike) if a <= STAR_UNLIKE]}


def _fit(read, on, yes_i, no_i, rect, slots):
    with ThreadPoolExecutor(SCAN_WORKERS) as pool:
        yes = list(pool.map(lambda i: read(on[i][0], rect), yes_i))
        no = list(pool.map(lambda i: read(on[i][0], rect), no_i))
    yes, no = [r for r in yes if r], [r for r in no if r]
    if len(yes) < cal.STAR_MIN_YES or len(no) < cal.STAR_MIN_NO:
        return None
    return cal.fit_stars(rect, yes, no, slots)


# --- Which frames go on the page ---

def analyse(frames, w, h, frame, read, slots=None, log=lambda m: None):
    """Everything the scan can tell. frames: [(time, small RGB)]; frame: the full picture's size; read(time, rect):
    a full-size area. Returns {"bars": coarse_bars or None, "stars": find_stars or None, "frames": [{"t", "black",
    "hud": True/False/None, "stars": True/False/None (None: can't tell)}]}."""
    raws = [r for _, r in frames]
    bars = coarse_bars(raws, w, h)
    info = [{"t": t, "black": is_black(r), "hud": None, "stars": None} for t, r in frames]
    stars = None
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
    if not found["bars"]:
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
