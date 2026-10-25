"""Unit checks for calibrate.py on made-up numbers, and one end-to-end run on a made-up video.

No GTA V value is used here: the stars, bars and phone are invented, so these checks can't leak gta5's numbers
into a re-calibration. The end-to-end check builds a 12 s 640x360 video with ffmpeg (two coloured bars and a
dark/bright/grey "phone" that come and go over a moving test pattern), labels 8 frames, draws the boxes, measures,
saves, and loads the result with clip.py. It works in a temporary folder; profiles/ is not touched.

Each check prints PASS or FAIL with what it tests. A check that raises counts as FAIL (with the error), so a
missing function shows as a failure, not a crash. The last line is RESULT: PASS or FAIL; exit code 0 or 1.

Usage:  python calibtest.py
"""

import json
import math
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


def made_up_video(path):
    """12 s, 640x360: two bars (green, blue) and a phone (dark face, bright icon, grey panel) over testsrc2.
    Shown 0-3 s and 6-9 s (phone 0-3 s only); 3-6 s and 9-12 s neither (stands in for a menu)."""
    on = "between(t,0,3)+between(t,6,9)"
    ph = "between(t,0,3)"
    vf = (f"drawbox=x=20:y=298:w=64:h=8:color=0x20c040:t=fill:enable='{on}',"
          f"drawbox=x=90:y=298:w=44:h=8:color=0x3070e0:t=fill:enable='{on}',"
          f"drawbox=x=496:y=196:w=112:h=96:color=0x202020:t=fill:enable='{ph}',"
          f"drawbox=x=504:y=200:w=56:h=12:color=0x040404:t=fill:enable='{ph}',"
          f"drawbox=x=560:y=220:w=16:h=8:color=0xe8e8e8:t=fill:enable='{ph}',"
          f"drawbox=x=504:y=248:w=40:h=24:color=0x5a5a5a:t=fill:enable='{ph}'")
    subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=30:duration=12", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "16", "-pix_fmt", "yuv420p", str(path)], check=True)


@check
def end_to_end_on_a_made_up_video():
    """add 8 frames, label, draw, measure (bars and phone ready, every frame right), save, clip.py loads it"""
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
                                 "phone": {"box": [496, 196, 112, 96], "dark": [506, 202, 50, 8], "bright": [562, 221, 10, 6],
                                           "grey": [[508, 252, 30, 16]]}}
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
