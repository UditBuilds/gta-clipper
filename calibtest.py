"""Unit checks for calibrate.py on made-up numbers and made-up HUDs, and two end-to-end runs on a made-up video.

No GTA V value is used here: the stars, bars and phone are invented, so these checks can't leak gta5's numbers
into a re-calibration. The bar finder is checked on 25 invented HUD layouts (2-4 bars, 3-10 px thick, any
height and colours, with or without a see-through edge, see-through bars, a bar that drains) mixed with frames
without the HUD. The end-to-end checks build a 12 s 640x360 video with ffmpeg (two coloured bars and a
dark/bright/grey "phone" that come and go over random colour blocks that change every frame, then a black
screen): one draws the bars by hand, the other finds them from one rough box and confirms the tool's guesses;
both measure, save and load the result with clip.py. They work in a temporary folder; profiles/ is not touched.

Each check prints PASS or FAIL with what it tests. A check that raises counts as FAIL (with the error), so a
missing function shows as a failure, not a crash. The last line is RESULT: PASS or FAIL; exit code 0 or 1.

Usage:  python calibtest.py
"""

import json
import math
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import calibrate as cal
import clip

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def raises(fn, *args):
    try:
        fn(*args)
    except cal.CalibError:
        return True
    return False


@check
def times_parse():
    """m:ss, h:mm:ss and seconds parse; 1:75 and words are refused"""
    return (cal.parse_time("1:40") == 100.0 and cal.parse_time("1:02:03.5") == 3723.5 and cal.parse_time("95.5") == 95.5
            and raises(cal.parse_time, "1:75") and raises(cal.parse_time, "soon"))


def star_clicks(first_x, tip_y, r, spacing, slots, inner, low_angle=54, inner_angle=-54):
    cx, cy = first_x, tip_y + r
    return ((first_x, tip_y), (first_x + spacing * (slots - 1), tip_y),
            (cx + r * math.cos(math.radians(low_angle)), cy + r * math.sin(math.radians(low_angle))),
            (cx + inner * r * math.cos(math.radians(inner_angle)), cy + inner * r * math.sin(math.radians(inner_angle))))


@check
def star_geometry_from_four_clicks():
    """4 clicks on a made-up star (tip 100,20, radius 10, spacing 25, 4 slots, inner 0.45) give its numbers back"""
    g = cal.star_geometry(*star_clicks(100.0, 20.0, 10.0, 25.0, 4, 0.45), 4)
    return (abs(g["first_tip_x"] - 100) < 0.01 and abs(g["tip_y"] - 20) < 0.01 and abs(g["radius"] - 10) < 0.01
            and abs(g["tip_spacing"] - 25) < 0.001 and abs(g["inner"] - 0.45) < 0.001)


@check
def star_geometry_any_bottom_tip_and_inner_corner():
    """the other bottom tip (126 degrees) and another inner corner (162 degrees) give the same star"""
    g = cal.star_geometry(*star_clicks(50.0, 8.0, 6.0, 15.0, 3, 0.4, low_angle=126, inner_angle=162), 3)
    return abs(g["radius"] - 6) < 0.01 and abs(g["inner"] - 0.4) < 0.001


@check
def star_geometry_refuses_bad_clicks():
    """a lowest point above the top point, or a last star left of the first, is refused"""
    top, last, low, inner = star_clicks(100.0, 20.0, 10.0, 25.0, 4, 0.45)
    return raises(cal.star_geometry, top, last, (100, 15), inner, 4) and raises(cal.star_geometry, top, (90, 20), low, inner, 4)


def refused_with(fn, *args):
    """The CalibError message fn raises, or '' if it doesn't raise one."""
    try:
        fn(*args)
    except cal.CalibError as e:
        return str(e)
    return ""


@check
def star_clicks_on_the_wrong_spots_name_the_point():
    """point 3 on another star, 1 slot typed with point 2 on another star, point 4 far outside: each refused, naming it"""
    top, last, low, inner = star_clicks(100.0, 20.0, 10.0, 25.0, 4, 0.45)
    far_low = (40.0, low[1])
    far_inner = (inner[0] + 5, inner[1] + 60)
    return ("point 3" in refused_with(cal.star_geometry, top, last, far_low, inner, 4)
            and "1 star slot" in refused_with(cal.star_geometry, top, last, low, inner, 1)
            and "point 4" in refused_with(cal.star_geometry, top, last, low, far_inner, 4)
            and "point 4" in refused_with(cal.star_geometry, top, last, low, (100.0, 30.0), 4))


@check
def star_outlines_past_the_box_are_refused():
    """a star box that cuts off the stars' top points gives a problem naming the top of the box"""
    clicks = dict(zip(("top_first", "top_last", "low", "inner"), star_clicks(512.0, 14.0, 10.0, 30.0, 4, 0.45)))
    drawing = {"bars": [{"box": [20, 300, 60, 4]}, {"box": [90, 300, 40, 4]}],
               "stars": {"box": [500, 20, 120, 30], "slots": 4, "clicks": clicks}, "phone": {"off": True}}
    _, notes = cal.geometry_profile("made_up", "Made Up", (640, 360), drawing)
    return "top" in dict(notes).get("stars", "") and "star box" in dict(notes)["stars"]


@check
def snapping_to_the_cell_grid():
    """phone box cut to whole cells; an area moves to the nearest cell edges, at least one cell, inside the box"""
    box = cal.snap_box([10.4, 20.2, 43.0, 17.9], (8, 4))
    a = cal.snap_area([13, 21, 10, 6], box, (8, 4))
    tiny = cal.snap_area([11, 21, 1, 1], box, (8, 4))
    edge = cal.snap_area([8, 18, 44, 20], box, (8, 4))  # less than a cell outside: cut to the box
    return box == [10, 20, 40, 16] and a == [10, 20, 16, 8] and tiny == [10, 20, 8, 4] and edge == box


@check
def a_phone_area_outside_the_box_is_refused():
    """an area drawn well outside the phone box is refused with a message naming it (it used to be cut down silently)"""
    box = cal.snap_box([100, 100, 80, 40], (8, 4))
    try:
        cal.snap_area([110, 150, 20, 30], box, (8, 4), "dark area")
    except cal.CalibError as e:
        return "dark area" in str(e) and "outside the phone box" in str(e)
    return False


@check
def bar_row_from_bar_boxes():
    """the minimap box takes the rows all bar boxes share; level boxes needed, no overlap side by side"""
    box, xr = cal.bar_row([[60, 10, 30, 6], [10, 11, 40, 6]])
    return (box == [10, 11, 80, 5] and xr == [[10, 50], [60, 90]] and raises(cal.bar_row, [[0, 0, 10, 3], [20, 5, 10, 3]])
            and raises(cal.bar_row, [[0, 0, 30, 4], [20, 0, 30, 4]]) and raises(cal.bar_row, [[0, 0, 30, 4]]))


@check
def limits_halfway_or_one_side():
    """at least: halfway to the nearest lower hidden value, else half the lowest shown; at most: the mirror image"""
    return (cal.at_least([50, 60], [10, 30, 70])[:2] == (40.0, "halfway")
            and cal.at_least([50, 60], [70, 90])[:2] == (25.0, "one side only")
            and cal.at_most([4, 6], [2, 20, 40])[:2] == (13.0, "halfway")
            and cal.at_most([4, 6], [1])[:2] == (14.0, "one side only"))


@check
def bar_colour_rule_from_frames():
    """a green bar: green must beat red and blue, limit halfway to the greenest hidden colour below the bar"""
    shown = [[40, 200, 50], [30, 180, 60]]
    hidden = [[100, 120, 100], [200, 50, 40]]
    any_of, rows = cal.fit_bar(1, shown, hidden, {})
    want = [[{"hi": "g", "lo": ["r", "b"], "min": (120 + 20) / 2}]]
    return any_of == want and rows[0]["how"] == "halfway"


@check
def a_bar_that_changes_colour_gets_two_groups():
    """a bar shown green on some frames and red on others passes on either colour"""
    any_of, _ = cal.fit_bar(1, [[30, 200, 40], [210, 40, 30]], [], {})
    return sorted(g[0]["hi"] for g in any_of) == ["g", "r"] and all(len(g) == 1 for g in any_of)


@check
def a_yellow_bar_tests_both_strong_channels():
    """a yellow bar (red and green strong, blue weak) gets two terms: red beats blue, green beats blue"""
    any_of, _ = cal.fit_bar(2, [[220, 200, 30], [210, 205, 40]], [], {})
    return len(any_of) == 1 and sorted((t["hi"], tuple(t["lo"])) for t in any_of[0]) == [("g", ("b",)), ("r", ("b",))]


@check
def dim_frames_of_a_bar_are_never_left_without_a_rule():
    """a green bar on dark and bright scenes (dim ones close to grey-green) gets one green rule that every shown frame
    passes; before, the dim frames got no rule at all and could never pass"""
    shown = [[60, 92, 58], [52, 84, 55], [90, 150, 86], [100, 160, 95], [70, 108, 66]]
    any_of, _ = cal.fit_bar(1, shown, [[30, 30, 30], [140, 120, 90]], {})
    rule = [[("rgb".index(t["hi"]), tuple("rgb".index(c) for c in t["lo"]), t["min"]) for t in g] for g in any_of]
    return (len(any_of) == 1 and [t["hi"] for t in any_of[0]] == ["g"]
            and all(clip.bar_colour_ok(c, rule) for c in shown))


@check
def every_coloured_frame_lands_in_one_group():
    """colour_groups: green and yellowish-green share one group, a red frame gets its own, a grey frame joins none"""
    cols = [[40, 200, 50], [35, 190, 45], [120, 180, 40], [100, 104, 98], [200, 40, 30]]
    groups = cal.colour_groups(cols)
    return (sorted(groups) == [(0,), (1,)] and len(groups[(1,)]) == 3 and groups[(0,)] == [[200, 40, 30]]
            and [100, 104, 98] not in groups[(1,)] + groups[(0,)])


@check
def a_black_frame_ticked_shown_does_not_wreck_the_limit():
    """a black bar on a frame ticked 'HUD shown' by mistake doesn't drag the limit down; it fails the rule instead"""
    shown = [[40, 200, 50], [50, 180, 60], [0, 0, 0]]
    any_of, _ = cal.fit_bar(1, shown, [[100, 120, 100]], {})
    rule = [[("rgb".index(t["hi"]), tuple("rgb".index(c) for c in t["lo"]), t["min"]) for t in g] for g in any_of]
    return any_of[0][0]["min"] == (120 + 20) / 2 and not clip.bar_colour_ok([0, 0, 0], rule)


@check
def a_wrong_frame_says_why():
    """hud_why names the bar that fails and the check it fails; phone_why names the failing phone part"""
    prof = SimpleNamespace(bar_rules=(((((1, (0, 2), 50.0),),)), ((((2, (0,), 40.0),),))), bar_min_sat=0.3,
                                bar_min_count=2, bar_min_distinct=30.0, phone_panel_range=(10.0, 60.0),
                                phone_face_max=8.0, phone_battery_min=90.0, phone_panel_spread=20.0, phone_panel_tint=10.0)
    hud = cal.hud_why([[40, 200, 50], [60, 100, 80]], prof)
    phone = cal.phone_why({"face_max": 30.0, "battery_mean": 120.0, "panel_min": 20.0, "panel_max": 30.0,
                           "panel_tint": 4}, prof)
    return ("1 of 2 bars pass" in hud and "bar 2" in hud and "blue beats red by 20, needs 40" in hud
            and phone == "dark area's brightest cell 30, needs at most 8")


@check
def a_grey_bar_has_no_rule():
    """a bar box drawn on grey gives no colour rule (the page then says so)"""
    any_of, _ = cal.fit_bar(1, [[120, 125, 118]], [], {})
    return any_of == []


@check
def an_override_wins():
    """a number typed in 'Use instead' replaces the fitted limit and is marked as set by you"""
    any_of, rows = cal.fit_bar(1, [[40, 200, 50]], [], {"bar1.g.g": 33.0})
    return any_of[0][0]["min"] == 33.0 and rows[0]["how"] == "set by you"


@check
def drawing_makes_a_profile_clip_loads():
    """a made-up drawing on a 640x360 frame becomes a profile clip.load_profile accepts, phone areas on the grid"""
    drawing = {"bars": [{"box": [20, 300, 60, 4]}, {"box": [90, 300, 40, 4]}],
               "stars": {"box": [500, 10, 120, 30], "slots": 4,
                         "clicks": dict(zip(("top_first", "top_last", "low", "inner"),
                                            star_clicks(512.0, 14.0, 10.0, 30.0, 4, 0.45)))},
               "phone": {"box": [500, 200, 100, 80], "dark": [505, 203, 50, 10], "bright": [560, 220, 9, 7],
                         "grey": [[505, 250, 30, 20]]}}
    prof, notes = cal.geometry_profile("made_up", "Made Up", (640, 360), drawing)
    with tempfile.TemporaryDirectory() as tmp:
        p = cal.load(prof, Path(tmp) / "made_up.json")
    return (not notes and p.frame == (640, 360) and p.bar_segments == ((20, 80), (90, 130)) and p.bar_min_count == 2
            and len(p.star_tips_x) == 4 and p.phone_box == (500, 200, 96, 80)
            and all((x0 - 500) % 8 == 0 and (y0 - 200) % 4 == 0 for x0, _, y0, _ in (p.phone_face, p.phone_battery)))


@check
def rules_switched_off():
    """stars and phone switched off: the profile loads with both rules off"""
    prof, notes = cal.geometry_profile("made_up", "Made Up", (640, 360),
                                       {"bars": [{"box": [20, 300, 60, 4]}, {"box": [90, 300, 40, 4]}],
                                        "stars": {"off": True}, "phone": {"off": True}})
    with tempfile.TemporaryDirectory() as tmp:
        p = cal.load(prof, Path(tmp) / "made_up.json")
    return not notes and not p.stars_on and not p.phone_on


def hsv(h, s, v):
    """RGB 0-255 from hue (degrees), saturation and value (0-1)."""
    c = v * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x), (0, x, c), (x, 0, c), (c, 0, x)][int(h // 60) % 6]
    return [round((t + v - c) * 255) for t in (r, g, b)]


def made_up_hud(seed, w=160, h=40, shown=14, hidden=5):
    """Frames (RGB bytes, w x h) of an invented HUD: 2-4 coloured bars in one row, 3-10 px thick, at a random
    height, maybe with a see-through dark edge round them, maybe see-through themselves, maybe one bar part-empty
    on some frames; over a random blocky scene that changes every frame. Plus frames with the scene only.
    Returns (frames with the HUD, frames without, true rows (y0, y1), true bars [(x0, x1)])."""
    rng = random.Random(seed)
    thick = rng.randint(3, 10)
    y0 = rng.randint(6, h - thick - 6)
    k = rng.randint(2, 4)
    hue0 = rng.uniform(0, 360)
    hues = [(hue0 + i * 360 / k + rng.uniform(-15, 15)) % 360 for i in range(k)]
    x, bars = rng.randint(4, 14), []
    for _ in range(k):
        bw = rng.randint(14, max(15, (w - 30) // k - 6))
        bars.append((x, x + bw))
        x += bw + rng.randint(2, 6)
    colours = [hsv(hu, rng.uniform(0.55, 0.95), rng.uniform(0.5, 0.9)) for hu in hues]
    edge, alpha, part_empty = rng.random() < 0.6, rng.choice([1.0, 1.0, 0.85]), rng.random() < 0.5

    def frame(with_hud):
        cell = rng.choice([4, 8, 16])
        light = rng.uniform(0.15, 1.0)
        # the first bar drains from the right on 30 % of frames (like health or a special ability running down)
        fill = rng.uniform(0.3, 0.9) if part_empty and rng.random() < 0.3 else 1.0
        blocks = {}
        px = bytearray(w * h * 3)
        for yy in range(h):
            for xx in range(w):
                key = (xx // cell, yy // cell)
                if key not in blocks:
                    blocks[key] = hsv(rng.uniform(0, 360), rng.uniform(0, 0.8), rng.uniform(0.1, 1.0) * light)
                c = [min(255, max(0, v + rng.randint(-6, 6))) for v in blocks[key]]
                if with_hud:
                    if edge and bars[0][0] - 2 <= xx < bars[-1][1] + 2 and y0 - 3 <= yy < y0 + thick + 3:
                        c = [round(v * 0.35) for v in c]
                    for i, (a, b) in enumerate(bars):
                        if a <= xx < b and y0 <= yy < y0 + thick:
                            full = colours[i]
                            if i == 0 and xx >= a + (b - a) * fill:
                                full = [round(v * 0.35) for v in full]  # the empty part of a bar
                            bright = 1 + rng.uniform(-0.08, 0.08)
                            c = [min(255, round((alpha * f * bright + (1 - alpha) * v))) for f, v in zip(full, c)]
                px[(yy * w + xx) * 3:(yy * w + xx) * 3 + 3] = bytes(c)
        return bytes(px)
    return [frame(True) for _ in range(shown)], [frame(False) for _ in range(hidden)], (y0, y0 + thick), bars


@check
def bars_found_on_made_up_huds():
    """on 25 invented HUD layouts, mixed with frames without the HUD, find_strips finds every bar, inside the bar,
    on rows inside the bars (at most an edge row lost each side)"""
    bad = []
    for seed in range(25):
        with_hud, without, (y0, y1), bars = made_up_hud(seed)
        frames = with_hud + without
        random.Random(seed).shuffle(frames)
        try:
            got = cal.find_bars_in(frames, 160, 40)
        except cal.CalibError as e:
            bad.append(f"seed {seed}: {e}")
            continue
        r0, r1 = got["rows"]
        rows_ok = y0 <= r0 < r1 <= y1 and r1 - r0 >= (y1 - y0) - 2
        bars_ok = len(got["bars"]) == len(bars) and all(a <= c < d <= b for (c, d), (a, b) in zip(got["bars"], bars))
        if not (rows_ok and bars_ok):
            bad.append(f"seed {seed}: rows {got['rows']} want inside {(y0, y1)}, bars {got['bars']} want inside {bars}")
    if bad:
        print("    " + "\n    ".join(bad))
    return not bad


@check
def no_bars_or_one_bar_is_refused():
    """a box with only scene in it, or with a single coloured bar, is refused with a plain message"""
    with_hud, without, _, _ = made_up_hud(3)
    one = made_up_hud(4)  # keep only its first bar by painting the others over with scene from a frame without the HUD
    w, h = 160, 40
    first_end = one[3][0][1]
    single = []
    for fr, bg in zip(one[0], one[1] * 3):
        px = bytearray(fr)
        for yy in range(h):
            px[(yy * w + first_end) * 3:(yy * w + w) * 3] = bg[(yy * w + first_end) * 3:(yy * w + w) * 3]
        single.append(bytes(px))
    return ("no coloured bars" in refused_with(cal.find_strips, without * 3, w, h)
            and "only one coloured bar" in refused_with(cal.find_strips, single, w, h))


@check
def hud_guess_from_the_found_bars():
    """the tool's guess is 'shown' on frames with the invented HUD and 'hidden' on frames without it"""
    with_hud, without, _, _ = made_up_hud(7)
    found = cal.find_bars_in(with_hud + without, 160, 40)
    return (all(cal.hud_guess(f, 160, found)[0] == "shown" for f in with_hud)
            and all(cal.hud_guess(f, 160, found)[0] == "hidden" for f in without))


@check
def label_summary_counts_guesses():
    """kept, changed, not sure and no-guess labels are counted per question"""
    frames = [{"labels": {"hud": "shown", "stars": "no", "phone": "?"}, "suggest": {"hud": "shown", "stars": "no", "phone": "no"}},
              {"labels": {"hud": "hidden", "stars": "yes", "phone": None}, "suggest": {"hud": "shown", "stars": "no", "phone": "no"}},
              {"labels": {"hud": "shown", "stars": None, "phone": None}, "suggest": {"hud": None, "stars": "no", "phone": "no"}}]
    s = cal.label_summary(frames)
    return (s["hud"] == {"guess_kept": 1, "guess_changed": 1, "not_sure": 0, "no_guess": 1}
            and s["stars"] == {"guess_kept": 1, "guess_changed": 1, "not_sure": 0, "no_guess": 0}
            and s["phone"] == {"guess_kept": 0, "guess_changed": 0, "not_sure": 1, "no_guess": 0})


def made_up_video(path):
    """12 s, 640x360: two bars (green, blue) and a phone (dark face, bright icon, grey panel) over a scene of random
    20 px colour blocks that changes every frame (a still test pattern would have coloured columns that never move,
    which nothing in a game does). Shown 0-3 s and 6-9 s (phone 0-3 s only); 3-6 s and 9-10.8 s neither (stands in
    for a menu); 10.8-12 s a black screen."""
    on = "between(t,0,3)+between(t,6,9)"
    ph = "between(t,0,3)"
    vf = ("scale=640:360:flags=neighbor,"
          f"drawbox=x=20:y=298:w=64:h=8:color=0x20c040:t=fill:enable='{on}',"
          f"drawbox=x=90:y=298:w=44:h=8:color=0x3070e0:t=fill:enable='{on}',"
          f"drawbox=x=496:y=196:w=112:h=96:color=0x202020:t=fill:enable='{ph}',"
          f"drawbox=x=504:y=200:w=56:h=12:color=0x040404:t=fill:enable='{ph}',"
          f"drawbox=x=560:y=220:w=16:h=8:color=0xe8e8e8:t=fill:enable='{ph}',"
          f"drawbox=x=504:y=248:w=40:h=24:color=0x5a5a5a:t=fill:enable='{ph}',"
          "drawbox=x=0:y=0:w=640:h=360:color=black:t=fill:enable='between(t,10.8,12)'")
    scene = "color=c=gray:s=32x18:r=30:d=12,format=yuv444p,noise=alls=100:allf=t+u"
    subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", scene,
                    "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", str(path)],
                   check=True)


PHONE_DRAWING = {"box": [496, 196, 112, 96], "dark": [506, 202, 50, 8], "bright": [562, 221, 10, 6],
                 "grey": [[508, 252, 30, 16]]}


@check
def end_to_end_bars_found_and_guesses_confirmed():
    """made-up video: the black frame is guessed black; one rough box finds both bars on their rows; the HUD guesses
    match the video; with the guesses confirmed (phone ones changed) Measure gets every frame right, and the saved
    profile says the bars were found by the tool and how many guesses were kept or changed"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        old = cal.CALIB_ROOT, clip.PROFILE_DIR
        cal.CALIB_ROOT, clip.PROFILE_DIR = tmp / "calib", tmp / "profiles"
        try:
            video = tmp / "made_up.mp4"
            made_up_video(video)
            s = cal.Session("made_up")
            s.add(video, ["0:01", "1.5", "2.5", "4", "5", "7", "8", "10.5", "11.5"])
            frames = s.data["frames"]
            black_ok = [f["black"] for f in frames] == [False] * 8 + [True]
            s.find_bars([10, 284, 140, 34])
            bars = [b["box"] for b in s.data["drawing"]["bars"]]
            bars_ok = (len(bars) == 2 and all(298 <= b[1] and b[1] + b[3] <= 306 for b in bars)
                       and 20 <= bars[0][0] and bars[0][0] + bars[0][2] <= 84
                       and 90 <= bars[1][0] and bars[1][0] + bars[1][2] <= 134)
            want = {f["id"]: "shown" if f["time_s"] < 3 or 6 <= f["time_s"] < 9 else "hidden" for f in frames}
            guesses_ok = all(f["suggest"]["hud"] == want[f["id"]] for f in frames)
            for f in frames:  # "Looks right" on every picture, then the 3 phone calls changed to "yes"
                f["labels"] = {k: f["suggest"][k] for k in ("hud", "stars", "phone")}
                if f["time_s"] < 3:
                    f["labels"]["phone"] = "yes"
            s.data["game"] = "Made Up"
            s.data["drawing"].update({"stars": {"off": True}, "phone": dict(PHONE_DRAWING)})
            report, _ = s.measure()
            frames_right = all(x["want"][k] == x["got"][k] for x in report["frames"] for k in ("hud", "phone"))
            out, _ = s.save_profile(force_with_problems=True)
            cal_block = json.loads(out.read_text(encoding="utf-8"))["calibration"]
        finally:
            cal.CALIB_ROOT, clip.PROFILE_DIR = old
    labels = cal_block["labels"]
    return (black_ok and bars_ok and guesses_ok and frames_right and cal_block["bars"].startswith("found by the tool")
            and labels["hud"]["guess_kept"] == 9 and labels["phone"] == {"guess_kept": 6, "guess_changed": 3,
                                                                         "not_sure": 0, "no_guess": 0})


@check
def end_to_end_on_a_made_up_video():
    """add 8 frames, label, draw the bars by hand, measure (bars and phone ready, every frame right), save, clip.py loads it"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        old = cal.CALIB_ROOT, clip.PROFILE_DIR
        cal.CALIB_ROOT, clip.PROFILE_DIR = tmp / "calib", tmp / "profiles"
        try:
            video = tmp / "made_up.mp4"
            made_up_video(video)
            s = cal.Session("made_up")
            s.add(video, ["0:01", "1.5", "2.5", "4", "5", "7", "8", "10.5"])
            for f in s.data["frames"]:
                t = f["time_s"]
                shown = t < 3 or 6 <= t < 9
                f["labels"] = {"hud": "shown" if shown else "hidden", "stars": None, "phone": "yes" if t < 3 else "no"}
            s.data["game"] = "Made Up"
            s.data["drawing"] = {"bars": [{"box": [24, 300, 56, 4]}, {"box": [94, 300, 36, 4]}], "stars": {"off": True},
                                 "phone": dict(PHONE_DRAWING)}
            report, _ = s.measure()
            frames_right = all(x["want"][k] == x["got"][k] for x in report["frames"] for k in ("hud", "phone"))
            statuses = {k: v["status"] for k, v in report["rules"].items()}
            out, _ = s.save_profile(force_with_problems=True)
            again = raises(s.save_profile, True)
            p = clip.load_profile("made_up")
            saved = json.loads(out.read_text(encoding="utf-8"))
        finally:
            cal.CALIB_ROOT, clip.PROFILE_DIR = old
    return (frames_right and statuses["minimap"] in ("ready", "check") and statuses["phone"] in ("ready", "check")
            and statuses["stars"] == "off" and again and p.phone_on and not p.stars_on
            and saved["calibration"]["frames"] == 8 and "inherited" in saved["calibration"])


def main():
    ok = True
    for fn in CHECKS:
        try:
            good, err = bool(fn()), ""
        except Exception as e:  # a missing function or field is a failure of this check, not of the run
            good, err = False, f"  ({type(e).__name__}: {e})"
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'} {fn.__name__}: {fn.__doc__}{err}")
    print("RESULT: " + ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
