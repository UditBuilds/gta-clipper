"""Checks for the footage indexer (index.py), the library search (library.py) and the shot-list (PR 21, session 1).

No Ollama and no footage are used. Most checks run on made-up model answers and HUD samples. The end-to-end checks
build a 48 s synthetic 1080p video with a GTA V-like HUD drawn where the gta5 profile expects it (the three bars
under the minimap; a row of wanted stars, 2 of them earned, for 12 s; 12 s with no HUD at all), run index.py on it
with a stand-in for the vision model, and check the segments, the files it writes, the cache and the search.
ffmpeg makes that video in a temporary folder (a few seconds).

Each check prints PASS or FAIL with what it tests. A check that raises counts as FAIL (with the error), so a missing
function shows as a failure, not a crash. The last line is RESULT: PASS or FAIL; exit code 0 or 1.

Usage:  python indextest.py
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import clip
import index
import library

HERE = Path(__file__).resolve().parent
CHECKS = []
PROF = clip.load_profile("gta5")
STATE = {}   # what the end-to-end run made, shared by the checks that look at it


def check(fn):
    CHECKS.append(fn)
    return fn


def answer(activity="driving", notable="Player drives a car down a street.", vehicle="car", police=False, conf=0.9):
    return {"notable": notable, "activity": activity, "vehicle": vehicle, "police_visible": police, "confidence": conf}


def record(*args, **kw):
    return {"answer": answer(*args, **kw), "error": None, "wall_s": 0.01}


def hud(n, stars=(), count=1, phone=(), no_minimap=(), on=True):
    """A HUD timeline of n half-second steps; stars/phone/no_minimap are (first step, end step) ranges."""
    def flags(ranges):
        return [any(a <= i < b for a, b in ranges) for i in range(n)]
    s = flags(stars)
    return {"on": on, "n": n, "minimap_share": 1.0, "stars": s, "count": [count if v else 0 for v in s],
            "phone": flags(phone), "no_minimap": flags(no_minimap)}


def star_row(earned, flashing=False):
    """{(x, y): brightness} of a drawn star row in the gta5 stars box: `earned` stars from the right have a black
    outline and a white (or grey, flashing) middle; the other slots are flat grey shapes."""
    x0, y0, w, h = PROF.star_box
    polys = index.star_polygons(PROF)
    px = {}
    for k, poly in enumerate(polys):
        is_earned = k >= len(polys) - earned
        for y in range(h):
            for x in range(w):
                cx, cy = x0 + x + 0.5, y0 + y + 0.5
                if index._inside(cx, cy, poly):
                    d = index._edge_dist(cx, cy, poly)
                    px[(x, y)] = (15 if d < 2.0 else 100 if flashing else 240) if is_earned else 140
    return px


def hud_frame(earned, flashing=False, background=90):
    """One raw HUD frame (clip.hud_filter's layout) whose stars box holds star_row(earned)."""
    raw = bytearray([background]) * PROF.frame_bytes
    for (x, y), v in star_row(earned, flashing).items():
        i = (y * PROF.hud_w + x) * 3
        raw[i:i + 3] = bytes([v, v, v])
    return bytes(raw)


# --- The model's answer ---

@check
def schema_is_strict_and_describes_first():
    """the schema asks for notable first, then activity (the 12 activities as an enum), vehicle, police_visible,
    confidence, all required; never_use is cutscene, menu_map, loading and phone"""
    s = index.SCHEMA
    return (list(s["properties"]) == ["notable", "activity", "vehicle", "police_visible", "confidence"]
            and s["required"] == list(s["properties"])
            and s["properties"]["activity"]["enum"] == list(index.ACTIVITIES)
            and set(index.ACTIVITIES) == {"driving", "on_foot", "shootout", "chase", "flying", "boat", "swimming",
                                          "cutscene", "menu_map", "phone", "loading", "other"}
            and set(index.NEVER_USE) == {"cutscene", "menu_map", "loading", "phone"})


@check
def a_good_answer_parses():
    """a reply that fits the schema gives the five fields, cleaned (spaces, case, extra keys dropped)"""
    a = index.parse_answer('{"notable": "  Car hits   a wall. ", "activity": "Driving", "vehicle": "Sports_Car", '
                           '"police_visible": true, "confidence": 0.8, "x": 1}')
    return a == {"notable": "Car hits a wall.", "activity": "driving", "vehicle": "sports car",
                 "police_visible": True, "confidence": 0.8}


@check
def bad_answers_are_refused():
    """not JSON, an unknown activity, police_visible that isn't true/false, a confidence that isn't a number and an
    empty notable line each raise ValueError"""
    good = {"notable": "x", "activity": "driving", "vehicle": "car", "police_visible": False, "confidence": 1}
    bad = ["not json", "[1]", json.dumps(dict(good, activity="racing")), json.dumps(dict(good, police_visible="no")),
           json.dumps(dict(good, confidence="high")), json.dumps(dict(good, confidence=True)),
           json.dumps(dict(good, notable="  "))]
    for text in bad:
        try:
            index.parse_answer(text)
            return False
        except ValueError:
            pass
    return True


@check
def notable_is_cut_to_15_words_and_values_are_tidied():
    """a notable line longer than 15 words is cut to 15; confidence is held to 0-1; an empty vehicle becomes none"""
    long = " ".join(f"w{i}" for i in range(22))
    a = index.parse_answer(json.dumps({"notable": long, "activity": "on_foot", "vehicle": "", "police_visible": False,
                                       "confidence": 7}))
    return (a["notable"] == " ".join(f"w{i}" for i in range(15)) + "..." and a["confidence"] == 1.0
            and a["vehicle"] == "none")


@check
def prompt_is_filled_with_the_profiles_game():
    """the prompt names the profile's game (GTA V for gta5) in place of {GAME}, and says nothing about the HUD's
    measured values; the request carries the schema, temperature 0 and one picture"""
    text, sha = index.load_prompt(PROF.game)
    body = index.build_request("m", text, "QUJD", [])
    return ("{GAME}" not in text and "GTA V" in text and len(sha) == 64 and body["format"] == index.SCHEMA
            and body["options"]["temperature"] == 0 and body["messages"][0]["images"] == ["QUJD"]
            and "think" not in body and index.build_request("m", text, "QUJD", ["thinking"])["think"] is False)


@check
def ask_retries_once_then_reports_the_error():
    """an unusable reply is asked again once with another seed; two unusable replies give an error record, not a
    crash; a usable second reply is taken"""
    with tempfile.TemporaryDirectory() as tmp:
        jpeg = Path(tmp) / "a.jpg"
        jpeg.write_bytes(b"x")
        seeds, replies = [], ["nope", json.dumps(answer())]

        def http(url, body=None, timeout=0):
            seeds.append(body["options"]["seed"])
            return {"message": {"content": replies[len(seeds) - 1]}}
        rec = index.ask_model("m", "p", jpeg, [], http=http)
        bad = index.ask_model("m", "p", jpeg, [], http=lambda url, body=None, timeout=0: {"message": {"content": "{"}})
    return (rec["error"] is None and rec["answer"]["activity"] == "driving" and seeds == [42, 43]
            and rec["attempts"] == 2 and bad["answer"] is None and bad["error"].startswith("unusable answer"))


# --- Wanted-star count ---

@check
def star_masks_cover_five_slots_without_overlap():
    """the gta5 profile gives 5 star slots, each with outline pixels and middle pixels inside the stars box, and
    no outline pixel belongs to two slots"""
    masks = index.star_masks(PROF)
    w, h = PROF.star_box[2], PROF.star_box[3]
    rings = [set(r) for r, _ in masks]
    return (len(masks) == 5 and all(len(r) >= 40 and len(c) >= 20 for r, c in masks)
            and all(0 <= i < w * h for r, c in masks for i in r + c)
            and not any(rings[a] & rings[b] for a in range(5) for b in range(a + 1, 5)))


@check
def drawn_stars_are_counted():
    """a drawn star row with 0, 1, 2, 3 and 5 earned stars is counted as 0, 1, 2, 3 and 5, white or flashing grey"""
    masks, rule = index.star_masks(PROF), index.star_count_rule(PROF)
    return all(index.count_stars(index.star_slots(hud_frame(n, flashing), PROF, masks), rule) == n
               for n in (0, 1, 2, 3, 5) for flashing in (False, True))


@check
def stars_count_from_the_filling_end_only():
    """stars are counted from the end the row fills from and stop at the first empty slot: an outline-like blob in
    the far slot alone is not a star; fill_from left counts from the other end"""
    rule = dict(index.STAR_COUNT)
    earned, empty = (40.0, 100.0), (120.0, 120.0)
    return (index.count_stars([empty, empty, empty, earned, earned], rule) == 2
            and index.count_stars([earned, empty, empty, empty, earned], rule) == 1
            and index.count_stars([earned, empty, empty, empty, empty], rule) == 0
            and index.count_stars([earned, earned, empty, empty, empty], dict(rule, fill_from="left")) == 2
            # bright digits in the row: the middle is brighter than the "outline", but not by the ratio a star has
            and index.count_stars([empty, empty, empty, empty, (131.0, 175.0)], rule) == 0)


@check
def a_profile_can_override_the_count_rule():
    """a profile's optional stars.count object changes the count rule (gta6 can fill from the left); gta5 has none,
    so the defaults apply; unknown keys in it are ignored"""
    other = SimpleNamespace(data={"stars": {"count": {"fill_from": "left", "max_ratio": 0.4, "junk": 1}}})
    rule = index.star_count_rule(other)
    return (rule["fill_from"] == "left" and rule["max_ratio"] == 0.4 and "junk" not in rule
            and index.star_count_rule(PROF) == index.STAR_COUNT)


# --- The HUD timeline ---

def samples(n, stars=(), count=1, minimap=None, phone=()):
    """n HUD samples half a second apart; stars/phone are (first, end) sample ranges, minimap a list of flags."""
    def at(ranges, i):
        return any(a <= i < b for a, b in ranges)
    return [(i * 0.5, 0.5 if at(stars, i) else 0.0, True if minimap is None else minimap[i], at(phone, i),
             count if at(stars, i) else 0) for i in range(n)]


@check
def short_star_gaps_are_bridged():
    """stars lost for 3 s between two star stretches still count as showing (with the lower count of the two
    sides); a 10 s gap stays a gap"""
    tl = index.hud_timeline(samples(120, stars=[(10, 30), (36, 50), (70, 80)], count=2), 60.0, PROF)
    return (all(tl["stars"][10:50]) and tl["count"][32] == 2 and not any(tl["stars"][52:68])
            and not any(tl["stars"][:8]))


@check
def hud_rules_switch_off_without_a_minimap():
    """a recording where the minimap is almost never seen has the HUD rules off: no stars, no phone, no excluded
    stretch, so only the model's labels count"""
    tl = index.hud_timeline(samples(120, stars=[(10, 60)], minimap=[i < 6 for i in range(120)]), 60.0, PROF)
    return (tl["on"] is False and not any(tl["stars"]) and not any(tl["no_minimap"]) and not any(tl["phone"])
            and sum(tl["count"]) == 0)


@check
def no_minimap_and_phone_stretches_are_separate():
    """4 s without the minimap is a no-minimap stretch (grown by the profile's 0.5 s pad), a 4 s phone call is a
    phone stretch, and neither is counted as the other"""
    mm = [not 20 <= i < 28 for i in range(120)]
    tl = index.hud_timeline(samples(120, minimap=mm, phone=[(60, 68)]), 60.0, PROF)
    return (all(tl["no_minimap"][19:29]) and not tl["no_minimap"][17] and not tl["no_minimap"][30]
            and all(tl["phone"][59:69]) and not any(tl["no_minimap"][55:75]) and not any(tl["phone"][15:35]))


# --- Merging into segments ---

@check
def hud_rules_win_over_the_model():
    """per half second: the phone's call screen makes it phone; no minimap makes it cutscene unless the model said
    menu_map or loading; a vehicle with wanted stars becomes chase; on foot with stars stays on foot"""
    h = hud(48, stars=[(0, 12), (36, 48)], phone=[(12, 24)], no_minimap=[(24, 36)])
    steps = index.step_labels(["driving", "driving", "menu_map", "on_foot"], 6.0, h)
    labels = [s[0] for s in steps]
    return (labels[:12] == ["chase"] * 12 and labels[12:24] == ["phone"] * 12
            and labels[24:36] == ["menu_map"] * 12 and labels[36:] == ["on_foot"] * 12
            and steps[0][1].startswith("hud") and steps[40][1] == "model"
            and index.step_labels(["driving"], 6.0, hud(12, no_minimap=[(0, 12)]))[0][0] == "cutscene"
            and index.step_labels(["boat"], 6.0, hud(12, stars=[(0, 12)]))[0][0] == "chase"
            and index.step_labels(["chase"], 6.0, hud(12))[0][0] == "chase")


@check
def only_a_lone_other_is_smoothed():
    """a lone 'other' sheet between two that agree takes their label; a lone shootout or on_foot sheet stands; a
    sheet with no usable answer takes its nearest neighbour's label"""
    return (index.smooth_labels(["driving", "other", "driving"]) == ["driving"] * 3
            and index.smooth_labels(["driving", "shootout", "driving"]) == ["driving", "shootout", "driving"]
            and index.smooth_labels(["driving", "on_foot", "driving"]) == ["driving", "on_foot", "driving"]
            and index.smooth_labels(["driving", "other", "on_foot"]) == ["driving", "other", "on_foot"]
            and index.fill_missing([None, "driving", None, None, "on_foot"]) ==
            ["driving", "driving", "driving", "on_foot", "on_foot"]
            and index.fill_missing([None, None]) == ["other", "other"])


@check
def short_gameplay_pieces_join_a_neighbour_but_never_use_is_left_alone():
    """a 1 s piece of gameplay joins its longer gameplay neighbour; a 1 s never_use piece stays, and a short piece
    next to it is not swallowed by it"""
    runs = index.absorb_short([["chase", 0, 20], ["driving", 20, 22], ["chase", 22, 40], ["cutscene", 40, 42],
                               ["on_foot", 42, 44], ["driving", 44, 60]], 4)
    return runs == [["chase", 0, 40], ["cutscene", 40, 42], ["driving", 42, 60]]


def made_up_index():
    """10 sheets of 6 s: driving (2 stars from 6 s to 20 s), a pause map at 30-36 s, a shootout, then a cutscene the
    HUD can't see (model only)."""
    times = [k * 6.0 for k in range(10)]
    recs = ([record("driving", f"Player drives a car, part {k}.") for k in range(5)]
            + [record("driving", "Map screen.")]
            + [record("shootout", "Player aims a gun at people near a store.", vehicle="none", police=True),
               record("on_foot", "Player walks past a body on the ground.", vehicle="none")]
            + [record("cutscene", "Two men talk in a close-up.", vehicle="none")] * 2)
    h = hud(120, stars=[(12, 40)], count=2, no_minimap=[(60, 72)])
    return times, recs, index.build_segments(times, recs, 6.0, 60.0, h), h


@check
def segments_cover_the_recording_in_order():
    """the segments run from 0 to the end with no gap or overlap, each with a length, an activity and a frame"""
    times, _, segs, _ = made_up_index()
    return (segs[0]["start_s"] == 0.0 and segs[-1]["end_s"] == 60.0
            and all(a["end_s"] == b["start_s"] for a, b in zip(segs, segs[1:]))
            and all(s["duration_s"] == round(s["end_s"] - s["start_s"], 2) and s["activity"] in index.ACTIVITIES
                    and s["thumb_t"] in times for s in segs))


@check
def segments_have_the_expected_labels():
    """driving, chase 6-20 s with max_stars 2, driving, a never_use cutscene cut exactly at the HUD's 30-36 s, a
    shootout, on foot, and a never_use cutscene from the model alone"""
    _, _, segs, _ = made_up_index()
    got = [(s["start_s"], s["end_s"], s["activity"], s["never_use"], s["max_stars"]) for s in segs]
    return got == [(0.0, 6.0, "driving", False, 0), (6.0, 20.0, "chase", False, 2), (20.0, 30.0, "driving", False, 0),
                   (30.0, 36.0, "cutscene", True, 0), (36.0, 42.0, "shootout", False, 0),
                   (42.0, 48.0, "on_foot", False, 0), (48.0, 60.0, "cutscene", True, 0)]


@check
def no_usable_segment_holds_hud_excluded_time():
    """no segment that isn't never_use contains a half second the HUD excluded (no minimap, or the phone up)"""
    _, _, segs, h = made_up_index()
    return all(s["never_use"] or not any(h["no_minimap"][i] or h["phone"][i]
                                         for i in range(int(s["start_s"] * 2), int(s["end_s"] * 2))) for s in segs)


@check
def segment_details_come_from_its_sheets():
    """a segment's notable lines are its sheets' lines (no repeats, with times), its vehicle the commonest one,
    police_visible true if any sheet saw police, and decided_by says model or the HUD rule"""
    _, _, segs, _ = made_up_index()
    chase, shoot = segs[1], segs[4]
    return ([n["text"] for n in chase["notable"]] == ["Player drives a car, part 1.", "Player drives a car, part 2."]
            and chase["notable"][0]["t"] == 6.0 and chase["vehicle"] == "car" and chase["stars_s"] == 14.0
            and chase["decided_by"] == "hud: vehicle with wanted stars" and chase["model_said"] == {"driving": 2}
            and shoot["police_visible"] is True and shoot["decided_by"] == "model" and shoot["vehicle"] == "none"
            and segs[3]["decided_by"] == "hud: no minimap")


@check
def a_star_count_needs_a_full_second():
    """max_stars is the highest count that showed for at least 1 s in the segment: a single half-second reading of
    4 stars doesn't make a 2-star chase a 4-star one"""
    h = hud(24, stars=[(0, 24)], count=2)
    h["count"][5] = 4
    segs = index.build_segments([0.0, 6.0], [record(), record()], 6.0, 12.0, h)
    return len(segs) == 1 and segs[0]["activity"] == "chase" and segs[0]["max_stars"] == 2


# --- The cache and memory guard ---

def run_labels(tmp, ask, ram=lambda: 16.0, times=(0.0, 6.0, 12.0), unload=lambda m: (0, True)):
    info = {"digest": "d1", "capabilities": []}
    return index.label_frames(Path(tmp) / "v.mp4", list(times), Path(tmp), "m", info, ("prompt", "sha"),
                              Path(tmp) / "cache", 6.0, 640, ask=ask, log=lambda m: None, ram=ram, unload=unload)


@check
def answers_are_cached_and_a_rerun_asks_nothing():
    """the first run asks the model once per sheet; a second run asks nothing and gives the same answers"""
    calls = []

    def ask(model, prompt, jpeg, caps):
        calls.append(jpeg.name)
        return record(notable=f"sheet {jpeg.name}")
    with tempfile.TemporaryDirectory() as tmp:
        first, s1 = run_labels(tmp, ask)
        second, s2 = run_labels(tmp, ask)
    return (len(calls) == 3 and s1["model_calls"] == 3 and s1["cache_hits"] == 0 and s2["model_calls"] == 0
            and s2["cache_hits"] == 3 and [r["answer"] for r in first] == [r["answer"] for r in second]
            and calls[1] == index.frame_name(6.0))


@check
def a_stopped_run_carries_on_and_errors_are_asked_again():
    """a run that dies at the 3rd sheet keeps the first two answers: the next run asks only for the rest; an
    unusable answer is not cached, so it is asked again"""
    calls = []

    def dies(model, prompt, jpeg, caps):
        if len(calls) == 2:
            raise index.IndexError_("Ollama stopped answering")
        calls.append(jpeg.name)
        return record()

    def bad_then_good(model, prompt, jpeg, caps):
        calls.append(jpeg.name)
        return {"answer": None, "error": "unusable answer: x", "wall_s": 0.01} if len(calls) == 3 else record()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            run_labels(tmp, dies)
            return False
        except index.IndexError_:
            pass
        _, s2 = run_labels(tmp, bad_then_good)
        _, s3 = run_labels(tmp, bad_then_good)
    return (s2["cache_hits"] == 2 and s2["model_calls"] == 1 and s2["model_errors"] == 1
            and s3["cache_hits"] == 2 and s3["model_calls"] == 1 and s3["model_errors"] == 0)


@check
def the_cache_key_covers_what_decides_the_answer():
    """another model digest, prompt, sheet time, sheet length or frame width is a different cache entry"""
    v = Path("nowhere.mp4")
    base = index.cache_fields(v, 6.0, 6.0, 640, "m", "d1", "sha", None)
    others = [index.cache_fields(v, 6.0, 6.0, 640, "m", "d2", "sha", None),
              index.cache_fields(v, 6.0, 6.0, 640, "m", "d1", "sha2", None),
              index.cache_fields(v, 12.0, 6.0, 640, "m", "d1", "sha", None),
              index.cache_fields(v, 6.0, 3.0, 640, "m", "d1", "sha", None),
              index.cache_fields(v, 6.0, 6.0, 320, "m", "d1", "sha", None)]
    return all(o != base for o in others) and base == index.cache_fields(v, 6.0, 6.0, 640, "m", "d1", "sha", None)


@check
def low_memory_unloads_the_model_then_stops_the_run():
    """with little free memory the model is unloaded (not more often than every RELOAD_MIN_CALLS calls) and the run
    goes on; if memory is still under the floor it stops with a message, and the answers so far stay cached"""
    unloads = []
    times = [k * 6.0 for k in range(25)]
    with tempfile.TemporaryDirectory() as tmp:
        _, stats = run_labels(tmp, lambda *a: record(), ram=lambda: 4.0, times=times,
                              unload=lambda m: unloads.append(m))
        try:
            run_labels(tmp + "b", lambda *a: record(), ram=lambda: 1.0, times=times)
            stopped = False
        except index.IndexError_ as e:
            stopped = "memory" in str(e)
    return stats["reloads"] == 2 and len(unloads) == 2 and stats["model_calls"] == 25 and stopped


@check
def held_out_footage_is_refused():
    """a video inside C:\\AI\\gta\\heldout is refused before anything is read; a video elsewhere is not off limits"""
    barred = Path(r"C:\AI\gta\heldout\heldout1.webm")
    try:
        index.index_video(barred) if barred.is_file() else None
        refused = not barred.is_file()
    except index.IndexError_ as e:
        refused = "held-out" in str(e)
    return (refused and index.off_limits(barred) is not None
            and index.off_limits(Path(r"C:\AI\gta\input\test1.webm")) is None)


@check
def the_indexer_never_reads_the_answer_keys():
    """index.py and library.py don't mention the answer keys or import eval: the keys are for measuring only"""
    for name in ("index.py", "library.py"):
        src = (HERE / name).read_text(encoding="utf-8")
        code = src.split('"""', 2)[2]   # the code after the module's docstring
        if "answer_keys" in code or "import eval" in code or "indexeval" in code or "KEY_DIR" in code:
            return False
    return True


# --- End to end: a synthetic video, a stand-in for the model ---

W, H, FPS, PART_S = 1920, 1080, 2, 12


def make_video(path):
    """48 s: bars only, bars + 2 earned stars, no HUD at all, bars again; 12 s each."""
    base = bytearray(bytes([90, 90, 90]) * (W * H))

    def rect(buf, x0, x1, y0, y1, rgb):
        row = bytes(rgb) * (x1 - x0)
        for y in range(y0, y1):
            buf[(y * W + x0) * 3:(y * W + x1) * 3] = row

    bars = bytearray(base)
    for (x0, x1), rgb in zip(PROF.bar_segments, [(60, 190, 60), (60, 150, 225), (225, 190, 40)]):
        rect(bars, x0, x1, PROF.bar_box[1] - 3, PROF.bar_box[1] + PROF.bar_box[3] + 3, rgb)
    stars = bytearray(bars)
    sx, sy = PROF.star_box[:2]
    for (x, y), v in star_row(2).items():
        i = ((sy + y) * W + sx + x) * 3
        stars[i:i + 3] = bytes([v, v, v])
    proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0", "-c:v", "libx264", "-preset", "ultrafast",
                             "-crf", "10", "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.PIPE)
    for part in (bars, stars, base, bars):
        frame = bytes(part)
        for _ in range(PART_S * FPS):
            proc.stdin.write(frame)
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg could not make the synthetic video")


def fake_ask(model, prompt, jpeg, caps):
    """The stand-in model: answers by the sheet's time, read from its file name. It calls the no-HUD part on_foot
    (wrong on purpose: the HUD rule must overrule it) and the last part a menu (a HUD blind spot)."""
    t = int(jpeg.stem.split("_")[1]) / 1000
    STATE.setdefault("asked", []).append(t)
    STATE.setdefault("sheet_bytes", {})[t] = jpeg.stat().st_size
    if t < 24:
        return record("driving", f"Player drives a car at {t:g} s.")
    if t < 36:
        return record("on_foot", "A man stands in a room.", vehicle="none")
    return record("menu_map", "Mission passed screen.", vehicle="none")


def end_to_end():
    """Make the video and index it twice (the second time from the cache); keep everything in STATE."""
    if "error" in STATE or "index" in STATE:
        return
    try:
        tmp = STATE["tmp"] = Path(tempfile.mkdtemp(prefix="indextest_"))
        video = tmp / "synthetic_hud.mp4"
        make_video(video)
        ollama = SimpleNamespace(check_ollama=lambda m: {"digest": "fake", "capabilities": [], "loaded": []},
                                 unload=lambda m: STATE.setdefault("unloads", []).append(m))
        lib = tmp / "library"
        logs = []
        STATE["index"] = index.index_video(video, library=lib, ask=fake_ask, ollama=ollama, log=logs.append,
                                           ram=lambda: 16.0)
        STATE["asked_first"] = len(STATE["asked"])
        STATE["again"] = index.index_video(video, library=lib, ask=fake_ask, ollama=ollama, log=logs.append,
                                           ram=lambda: 16.0)
        STATE["lib"], STATE["video"], STATE["logs"] = lib, video, logs
    except Exception as e:
        STATE["error"] = f"{type(e).__name__}: {e}"
        raise


def e2e(fn):
    """A check that needs the end-to-end run."""
    def wrapped():
        end_to_end()
        if "error" in STATE:
            raise RuntimeError("the end-to-end run failed: " + STATE["error"])
        return fn()
    wrapped.__name__, wrapped.__doc__ = fn.__name__, fn.__doc__
    return check(wrapped)


@e2e
def e2e_sheets_are_cut_at_the_right_times():
    """48 s of video gives 8 sheets at 0, 6, ... 42 s, each a 1280x720 JPEG of 4 frames, and 96 HUD samples"""
    idx = STATE["index"]
    sheet = STATE["lib"] / "synthetic_hud" / idx["sheets"][3]["file"]
    size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", str(sheet)], capture_output=True, text=True).stdout.strip()
    saved = json.loads((STATE["lib"] / "synthetic_hud" / "hud.json").read_text(encoding="utf-8"))
    return ([s["t"] for s in idx["sheets"]] == [k * 6.0 for k in range(8)] and size == "1280,720"
            and len(saved["samples"]) == 96 and sorted(STATE["asked"][:8]) == [k * 6.0 for k in range(8)])


@e2e
def e2e_the_hud_is_read_from_the_picture():
    """the HUD rules are on; the drawn stars are read as 2 stars for about 12 s; the 12 s with no bars are found as
    a no-minimap stretch"""
    hud_ = STATE["index"]["hud"]
    return (hud_["on"] is True and 11.0 <= hud_["stars_s"] <= 13.0 and 12.0 <= hud_["no_minimap_s"] <= 13.5
            and hud_["phone_s"] == 0 and [s["hud_stars"] for s in STATE["index"]["sheets"]][2:4] == [2, 2])


@e2e
def e2e_segments_follow_the_hud_and_the_model():
    """driving 0-12 s; chase with max_stars 2 while the stars show (the model only said driving); the no-HUD part is
    a never_use cutscene although the model said on_foot; the model's menu is never_use too"""
    got = [(s["activity"], s["never_use"], s["max_stars"]) for s in STATE["index"]["segments"]]
    segs = STATE["index"]["segments"]
    return (got == [("driving", False, 0), ("chase", False, 2), ("cutscene", True, 0), ("menu_map", True, 0)]
            and abs(segs[1]["start_s"] - 12.0) <= 0.5 and abs(segs[2]["start_s"] - 24.0) <= 0.5
            and abs(segs[3]["start_s"] - 36.0) <= 0.5 and segs[3]["end_s"] == 48.0
            and segs[1]["decided_by"].startswith("hud") and segs[2]["decided_by"] == "hud: no minimap"
            and segs[2]["model_said"] == {"on_foot": 2})


@e2e
def e2e_files_are_written():
    """index.json, review.html (never_use rows marked, one row per segment, sheets linked) and library.json (one
    entry for the video, with its profile and model) are written; the model is unloaded at the end of each run"""
    folder = STATE["lib"] / "synthetic_hud"
    page = (folder / "review.html").read_text(encoding="utf-8")
    lib = json.loads((STATE["lib"] / "library.json").read_text(encoding="utf-8"))
    entry = lib["videos"]["synthetic_hud"]
    saved = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    return (saved["segments"] == STATE["again"]["segments"] and page.count('<tr class="never">') == 2
            and page.count('<tr class="ok">') == 2 and "NEVER USE" in page and "frames/f_" in page
            and list(lib["videos"]) == ["synthetic_hud"] and entry["profile"] == "gta5" and entry["game"] == "GTA V"
            and entry["segments"] == 4 and entry["model"] == index.MODEL and 23.0 <= entry["never_use_s"] <= 25.0
            and len(STATE["unloads"]) == 2)


@e2e
def e2e_a_rerun_is_cached_and_keeps_the_first_runs_timing():
    """the second run asks the model nothing, redoes no decode, gives the same segments, and keeps the timing of
    the run that did the work"""
    first, again = STATE["index"], STATE["again"]
    return (STATE["asked_first"] == 8 and len(STATE["asked"]) == 8 and again["segments"] == first["segments"]
            and first["timing_s"]["model_calls"] == 8 and again["timing_s"]["model_calls"] == 8
            and "last_rerun_total" in again["timing_s"]
            and any("already there" in line for line in STATE["logs"]))


@e2e
def e2e_search_finds_the_chase():
    """library.find on the new library: chase with 2+ stars and 6+ s finds the one chase; never_use segments are
    left out unless asked for; --activity takes several values"""
    idxs = library.load_library(STATE["lib"])
    chase = library.find(idxs, activity=["chase"], min_stars=2, min_len=6)
    return (len(chase) == 1 and chase[0]["video"] == "synthetic_hud" and chase[0]["max_stars"] == 2
            and chase[0]["path"] == str(STATE["video"].resolve())
            and len(library.find(idxs)) == 2 and len(library.find(idxs, exclude_never_use=False)) == 4
            and len(library.find(idxs, activity=["chase", "driving"])) == 2
            and library.find(idxs, activity=["cutscene"]) == []
            and len(library.find(idxs, min_stars=3)) == 0)


# --- library.py on made-up indexes ---

def made_up_library():
    times, recs, segs, _ = made_up_index()
    one = {"video": {"stem": "vidA", "path": r"C:\x\vidA.webm", "duration_s": 60.0},
           "profile": {"name": "gta5", "game": "GTA V"}, "indexed": "2026-11-13T00:00:00", "segments": segs}
    two = {"video": {"stem": "vidB", "path": r"C:\x\vidB.webm", "duration_s": 60.0},
           "profile": {"name": "gta6", "game": "GTA VI"}, "indexed": "2026-11-20T00:00:00", "segments": segs[:2]}
    return [one, two]


@check
def find_filters_by_every_key():
    """find filters by activity, stars, length, vehicle, police, text, the segment before, video and profile"""
    lib = made_up_library()
    f = library.find
    return ([r["video"] for r in f(lib, activity=["chase"])] == ["vidA", "vidB"]
            and len(f(lib, activity=["chase"], profile="gta6")) == 1
            and len(f(lib, min_stars=2)) == 2 and len(f(lib, min_stars=1, max_stars=1)) == 0
            and [r["activity"] for r in f(lib, video=["vidA"], min_len=10)] == ["chase", "driving"]
            and [r["activity"] for r in f(lib, video=["vidA"], max_len=6)] == ["driving", "shootout", "on_foot"]
            and len(f(lib, vehicle=["car"], video=["vidA"])) == 3 and len(f(lib, police=True)) == 1
            and [r["activity"] for r in f(lib, text=["BODY", "nothing"])] == ["on_foot"]
            and [r["start_s"] for r in f(lib, after=["chase"])] == [20.0]
            and [r["activity"] for r in f(lib, video=["vidA"], after=["cutscene"], exclude_never_use=False)]
            == ["shootout"])


@check
def never_use_is_left_out_by_default():
    """find leaves never_use segments out unless exclude_never_use is false; asking for cutscenes finds nothing
    by default"""
    lib = made_up_library()[:1]
    return (not any(r["never_use"] for r in library.find(lib)) and len(library.find(lib)) == 5
            and len(library.find(lib, exclude_never_use=False)) == 7 and library.find(lib, activity=["cutscene"]) == []
            and len(library.find(lib, activity=["cutscene"], exclude_never_use=False)) == 2)


@check
def a_wrong_query_is_an_error():
    """an unknown query key, an unknown activity and a min_stars that isn't a number each raise LibraryError"""
    for q in ({"speed": 3}, {"activity": ["racing"]}, {"min_stars": "two"}, {"after": ["flying", "x"]},
              {"exclude_never_use": "yes"}):
        try:
            library.find(made_up_library(), **q)
            return False
        except library.LibraryError:
            pass
    return True


@check
def the_shotlist_holds_only_shots():
    """shotlist.json loads; it has the six launch-week topics and at least one more; every topic is 'verify after
    launch'; topics and shots hold nothing but ids, titles, what to look for and a valid query (no facts)"""
    data = library.load_shotlist()
    ids = [t["id"] for t in data["topics"]]
    needed = {"wanted-system", "police-chase", "driving-vehicles", "combat-weapons", "exploring-map", "heist-robbery"}
    return (needed <= set(ids) and len(ids) > len(needed) and len(ids) == len(set(ids))
            and all(t["status"] == "verify after launch" and set(t) == {"id", "title", "status", "shots"}
                    for t in data["topics"])
            and all(set(s) == {"id", "want", "query"} for t in data["topics"] for s in t["shots"])
            and "claim" not in json.dumps(data["topics"]).lower())


@check
def a_shotlist_topic_must_be_marked_verify_after_launch():
    """a shot-list whose topic has another status, or a shot with a bad query, is refused"""
    with tempfile.TemporaryDirectory() as tmp:
        ok = {"id": "a", "title": "A", "status": "verify after launch",
              "shots": [{"id": "s", "want": "w", "query": {"activity": ["chase"]}}]}
        for n, topic in enumerate([dict(ok, status="confirmed"), dict(ok, shots=[]),
                                   dict(ok, shots=[{"id": "s", "want": "w", "query": {"activity": ["racing"]}}])]):
            path = Path(tmp) / f"{n}.json"
            path.write_text(json.dumps({"topics": [topic]}), encoding="utf-8")
            try:
                library.load_shotlist(path)
                return False
            except library.LibraryError:
                pass
        path = Path(tmp) / "ok.json"
        path.write_text(json.dumps({"topics": [ok]}), encoding="utf-8")
        return len(library.load_shotlist(path)["topics"]) == 1


@check
def coverage_counts_segments_per_topic():
    """coverage runs each shot's query: a topic's segment count is its distinct segments over all its shots, and
    a profile limits it to recordings indexed with that profile"""
    shotlist = {"topics": [{"id": "t", "title": "T", "status": "verify after launch", "shots": [
        {"id": "a", "want": "chase", "query": {"activity": ["chase"]}},
        {"id": "b", "want": "2 stars", "query": {"min_stars": 2}},
        {"id": "c", "want": "flying", "query": {"activity": ["flying"]}}]}]}
    cov = library.coverage(made_up_library(), shotlist)[0]
    only5 = library.coverage(made_up_library(), shotlist, profile="gta5")[0]
    return (cov["segments"] == 2 and cov["shots_covered"] == 2 and [s["found"] for s in cov["shots"]] == [2, 2, 0]
            and cov["shots"][0]["seconds"] == 28.0 and only5["segments"] == 1)


def main():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ERROR: ffmpeg/ffprobe not found on PATH")
        sys.exit(1)
    ok = True
    try:
        for fn in CHECKS:
            try:
                good, err = bool(fn()), ""
            except Exception as e:  # a missing function or field is a failure of this check, not of the run
                good, err = False, f"  ({type(e).__name__}: {e})"
            ok &= good
            print(f"  {'PASS' if good else 'FAIL'} {fn.__name__}: {' '.join(fn.__doc__.split())}{err}", flush=True)
    finally:
        if "tmp" in STATE:
            shutil.rmtree(STATE["tmp"], ignore_errors=True)   # the folder this test made
    print("RESULT: " + ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
