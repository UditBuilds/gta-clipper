"""Make a HUD profile (profiles/<name>.json) for clip.py from a few labelled frames of a recording (v9).

  1. --add grabs one frame of a video at each time you give (as you'd see it, after the container crop).
  2. A local page (http://127.0.0.1, this machine only) walks you through it in plain English: you tick what each
     frame shows (HUD shown or hidden, wanted stars or not, phone call screen or not), then draw the star row,
     the coloured bars by the minimap and the phone on zoomed frames.
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

import clip

CALIB_ROOT = Path(r"C:\AI\gta\calib")
NAME_RE = re.compile(r"^[a-z0-9_]{1,40}$")
# Values a still frame can't show. They are GTA V's (gta5's), the only ones we have; the profile lists them.
DEFAULTS = {"stretch_min_s": 1.5, "stretch_pad_s": 0.5, "min_share": 0.2, "star_boost": 3.0, "phone_cell": [8, 4]}
STRONG_GAP = 40.0        # a bar's colour channel within this of its brightest channel counts as one of its colours
SIN54 = math.sin(math.radians(54))  # a 5-pointed star's lowest tips sit SIN54 * radius below its centre
WORKERS = 4              # ffmpeg frame grabs at once


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
    lowest point and one of its inner corners. The star is clip.py's: 5 points, the top one straight up."""
    (ax, ay), (bx, by) = top_first, top_last
    r = (low[1] - ay) / (1 + SIN54)
    if r <= 1:
        raise CalibError("the star's lowest point must be below its top point: click the top tip, then a bottom tip")
    spacing = (bx - ax) / (slots - 1) if slots > 1 else 0.0
    if slots > 1 and spacing <= r:
        raise CalibError("the last star's top point must be to the right of the first star, by more than a star's width")
    ratio = math.hypot(inner[0] - ax, inner[1] - (ay + r)) / r
    if not 0.15 <= ratio <= 0.85:
        raise CalibError("the inner corner should be a notch between two points of the first star; click it again")
    return {"first_tip_x": round(ax, 2), "tip_spacing": round(spacing, 3), "tip_y": round(ay, 2),
            "radius": round(r, 2), "inner": round(ratio, 3), "level_px": round(by - ay, 1)}


def snap_box(rect, cell):
    """[x, y, w, h] in whole pixels, w and h cut down to whole cells (at least one)."""
    x, y, w, h = (round(v) for v in rect)
    return [x, y, max(cell[0], w // cell[0] * cell[0]), max(cell[1], h // cell[1] * cell[1])]


def snap_area(rect, box, cell):
    """rect moved onto the box's cell grid (nearest cell edges, at least one cell) and kept inside the box."""
    bx, by, bw, bh = box
    cw, ch = cell
    x, y, w, h = rect

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


def colour_groups(colours):
    """Shown colours of one bar grouped by which channels are its colours (within STRONG_GAP of the brightest).
    A bar that changes colour (e.g. health turning red) gets one group per colour."""
    groups = {}
    for c in colours:
        top = max(c)
        groups.setdefault(tuple(i for i in range(3) if c[i] >= top - STRONG_GAP), []).append(c)
    return groups


CH = "rgb"
CH_NAME = {"r": "red", "g": "green", "b": "blue"}


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
    reported; the minimap needs its bars before anything can be measured."""
    d = drawing
    notes = []
    bars = d.get("bars") or []
    try:
        mm_box, xr = bar_row([b["box"] for b in bars])
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
        c = st.get("clicks") or {}
        try:
            if not st.get("box"):
                raise CalibError("draw the box around the star row")
            if not isinstance(st.get("slots"), int) or not 1 <= st["slots"] <= 10:
                raise CalibError("type how many star slots the row has")
            missing = [k for k in ("top_first", "top_last", "low", "inner") if not c.get(k)]
            if missing:
                raise CalibError("click the 4 star points (" + ", ".join(missing) + " still missing)")
            g = star_geometry(c["top_first"], c["top_last"], c["low"], c["inner"], st["slots"])
            box = [round(v) for v in st["box"]]
            profile["stars"] = {"on": True, "box": box, "slots": st["slots"],
                                **{k: g[k] for k in ("first_tip_x", "tip_spacing", "tip_y", "radius", "inner")},
                                "min_score": 1.0, "boost": DEFAULTS["star_boost"]}
            if abs(g["level_px"]) > 2:
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
        box = snap_box(ph["box"], cell)
        profile["phone"] = {"on": True, "box": box, "cell": list(cell),
                            "dark": {"area": snap_area(ph["dark"], box, cell), "max": 0.0},
                            "bright": {"area": snap_area(ph["bright"], box, cell), "min_mean": 0.0},
                            "grey": {"areas": [snap_area(a, box, cell) for a in ph["grey"]], "min": 0.0, "max": 0.0,
                                     "max_spread": 0.0, "max_tint": 0.0}}
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
        self.save()

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
        frames = [f for f in d["frames"] if any(v is not None for v in f["labels"].values())]
        if not frames:
            raise CalibError("tick what at least some frames show (step 1)")
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
            sat_yes = [clip.saturation(means[f][i]) for f in hud_yes for i in range(len(means[f]))]
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
                            f"scores {max(no):.3f}. Check the labels, the star box and the 4 clicks")
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
        for f in frames:
            fid, lb = f["id"], lab[f["id"]]
            raw = raws[fid]
            got = {"hud": "shown" if clip.minimap_seen(raw, final) else "hidden"}
            if final.stars_on:
                got["stars"] = "yes" if clip.star_score(raw, final) >= final.star_min_score else "no"
            if final.phone_on:
                got["phone"] = "yes" if clip.phone_seen(raw, final) else "no"
            report["frames"].append({"id": fid, "label": f"{f['video_name']} {f['time']}", "want": lb, "got": got,
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
            raise CalibError("not ready to save: " + ("type the game's name (step 1); " if not self.data.get("game")
                                                       else "") + "fix the rules marked Problem first")
        if not report["clean"] and not force_with_problems:
            raise CalibError("some frames still come out wrong or a limit is one-sided; press 'Save anyway' to keep it")
        out = clip.PROFILE_DIR / f"{self.name}.json"
        if out.exists():
            raise CalibError(f"{out} already exists; calibrate.py never overwrites a profile. Pick another name")
        log = self.data["log"]
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
                    if self.path in ("/state", "/measure", "/save"):
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
    except CalibError as e:
        sys.exit("ERROR: " + str(e))
    if not session.data["frames"]:
        sys.exit("ERROR: no frames yet: use --add <video> <time> ...")
    print(f"Session {session.file}: {len(session.data['frames'])} frame(s)")
    serve(session, args.port, not args.no_browser)


if __name__ == "__main__":
    main()
