"""Checks for PR 22, session 2: the guided calibration (calibrate.py --guided, calibrate_guided.py), the library
search fixes (library.py) and Shorts built from the owner's own footage (footage_short.py).

No Ollama, no Kokoro voice and none of the owner's footage are used: the HUDs, the recordings, the model's answers
and the voice's timings are all made up here. The guided-calibration checks draw their own HUD (three coloured bars,
a row of five stars, a line of changing text) at a place and size no profile has, so nothing of GTA V's layout can
leak into what the scan finds.

Each check prints PASS or FAIL with what it tests. A check that raises counts as FAIL (with the error), so a missing
function shows as a failure, not a crash. The last line is RESULT: PASS or FAIL; exit code 0 or 1.

Usage:  python footagetest.py          (2 checks of explainer.py's sound are skipped with plain .venv)
        .venv-tts\\Scripts\\python.exe footagetest.py     every check
"""

import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import calibrate as cal
import calibrate_guided as guided
import calibtest
import clip
import footage_short
import index
import library
import shorttest

HERE = Path(__file__).resolve().parent
CHECKS = []
STATE = {}


def check(fn):
    CHECKS.append(fn)
    return fn


# --- A made-up HUD, drawn at full size and looked at the way the scan does (every 4th pixel) ---

FW, FH = 960, 540
BARS = [((36, 150), (30, 190, 70)), ((166, 222), (40, 110, 230)), ((238, 300), (225, 190, 40))]   # x range, colour
BAR_Y = (498, 506)
STAR = {"tip_x": 764.0, "spacing": 31.0, "tip_y": 28.0, "r": 15.0, "inner": 0.42, "slots": 5}
STAR_BOX = (740, 22, 164, 40)        # the row with a little space round it: only for drawing
TEXT_BOX = (300, 440, 360, 20)       # a line of "subtitles": white strokes that change on every frame


def scene(rng, block=24):
    """A full-size frame of random colour blocks (a changing scene), as a bytearray of RGB."""
    rows = []
    for _ in range(FH // block + 1):
        row = b"".join(bytes([rng.randrange(256), rng.randrange(256), rng.randrange(256)]) * block
                       for _ in range(FW // block + 1))[:FW * 3]
        rows.append(row * block)
    return bytearray(b"".join(rows)[:FW * FH * 3])


def fill(frame, x0, x1, y0, y1, colour):
    for y in range(y0, y1):
        frame[(y * FW + x0) * 3:(y * FW + x1) * 3] = bytes(colour) * (x1 - x0)


def draw_region(frame, box, painter):
    """Let painter change the pixels of box ([r, g, b] lists, as calibtest's drawing helpers use)."""
    x0, y0, w, h = box
    px = [list(frame[(y * FW + x) * 3:(y * FW + x) * 3 + 3]) for y in range(y0, y0 + h) for x in range(x0, x0 + w)]
    out = calibtest.to_bytes(painter(px, w, h))
    for y in range(h):
        frame[((y0 + y) * FW + x0) * 3:((y0 + y) * FW + x0 + w) * 3] = out[y * w * 3:(y + 1) * w * 3]


def made_up_frame(rng, hud=True, lit=0, text=False, black=False, sky=False):
    """One full-size frame: the bars when the HUD is on, the star row with `lit` stars earned when lit > 0."""
    if black:
        return bytearray(FW * FH * 3)
    frame = scene(rng)
    if sky:   # a big patch of one colour that is there on most frames: steady colour, but no thin strip
        fill(frame, 0, FW, 60, 200, (70, 120, 220))
    if hud:
        for (x0, x1), colour in BARS:
            fill(frame, x0, x1, BAR_Y[0], BAR_Y[1], colour)
    if lit:
        x0, y0 = STAR_BOX[:2]
        stars = [calibtest.star_points(STAR["tip_x"] - x0 + k * STAR["spacing"], STAR["tip_y"] - y0, STAR["r"],
                                       STAR["inner"]) for k in range(STAR["slots"])]
        draw_region(frame, STAR_BOX, lambda px, w, h: calibtest.draw_stars(w, h, stars, lit, px))
    if text:
        draw_region(frame, TEXT_BOX, lambda px, w, h: calibtest.digits(px, w, h, rng))
    return frame


def small(frame):
    """The scan's view of a frame: every 4th pixel of every 4th row."""
    d = guided.DIV
    return b"".join(bytes(frame[(y * FW + x) * 3:(y * FW + x) * 3 + 3]) for y in range(0, FH - FH % d, d)
                    for x in range(0, FW - FW % d, d))


def made_up_recording(seed=7, n=40, hud_share=0.45, starred=8):
    """n frames, one per 10 s: the HUD on in under half of them (a first session is mostly cutscenes), the star row
    in `starred` of those, text on a third of all, two black screens. Returns (frames by time, small frames, facts)."""
    rng = random.Random(seed)
    order = list(range(n))
    rng.shuffle(order)
    black = set(order[:2])
    hud_on = set(order[2:2 + round(hud_share * n)])
    stars = {i: rng.randint(1, 3) for i in sorted(hud_on)[::2][:starred]}
    full, facts = {}, {}
    for i in range(n):
        t = 5.0 + 10.0 * i
        full[t] = made_up_frame(rng, hud=i in hud_on, lit=stars.get(i, 0), text=rng.random() < 0.33, black=i in black,
                                sky=i % 4 != 0)
        facts[t] = {"hud": i in hud_on, "stars": i in stars, "black": i in black}
    return full, [(t, small(f)) for t, f in full.items()], facts


def read_area(full):
    def read(t, rect):
        x, y, w, h = rect
        f = full[t]
        return b"".join(bytes(f[((y + j) * FW + x) * 3:((y + j) * FW + x + w) * 3]) for j in range(h))
    return read


def scanned():
    """The made-up recording, analysed once and shared by the checks below."""
    if "found" not in STATE:
        full, frames, facts = made_up_recording()
        STATE.update(full=full, frames=frames, facts=facts,
                     found=guided.analyse(frames, FW // guided.DIV, FH // guided.DIV, (FW, FH), read_area(full),
                                          slots=STAR["slots"]))
    return STATE


# --- Guided calibration: the scan ---

@check
def scan_times_cover_the_recording():
    """the scan's looks are spread evenly from just after the start to just before the end: 120 of them on a long
    recording, fewer on a short one (never closer than half a second), and they never reach past the end"""
    long, short = guided.scan_times(3600.0), guided.scan_times(40.0)
    gaps = [b - a for a, b in zip(long, long[1:])]
    return (len(long) == 120 and long[0] <= 2 and 3597 <= long[-1] < 3600 and max(gaps) - min(gaps) < 0.02
            and 3 <= len(short) <= 80 and min(b - a for a, b in zip(short, short[1:])) >= 0.5 and short[-1] < 40)


@check
def bars_are_found_with_no_hint_and_the_hud_on_under_half_the_time():
    """made-up HUD at a place no profile has, on screen in 45 % of the frames: the scan finds the row of 3 bars
    (each strip inside its bar, give or take the 4 px it samples at), and says which frames show the HUD; a big
    patch of steady colour that is no thin strip is not taken for a bar"""
    s = scanned()
    bars = s["found"]["bars"]
    if not bars or len(bars["strips"]) != 3:
        return False
    d = guided.DIV
    inside = all(x0 - d <= st["x"][0] * d and st["x"][1] * d <= x1 + d and BAR_Y[0] - d <= st["y"][0] * d
                 and st["y"][1] * d <= BAR_Y[1] + d for st, ((x0, x1), _) in zip(bars["strips"], BARS))
    hud_ok = all(bool(f["hud"]) == s["facts"][f["t"]]["hud"] for f in s["found"]["frames"])
    black_ok = all(f["black"] == s["facts"][f["t"]]["black"] for f in s["found"]["frames"])
    return inside and hud_ok and black_ok


@check
def no_bars_means_no_guesses():
    """a recording with no coloured bars at all: the scan says so (None) instead of inventing a row, and the page
    then gets an even spread of pictures with no HUD guess, for the owner to drag the box"""
    rng = random.Random(3)
    frames = [(float(i), small(made_up_frame(rng, hud=False))) for i in range(30)]
    found = guided.analyse(frames, FW // guided.DIV, FH // guided.DIV, (FW, FH), lambda t, rect: None)
    picks = guided.choose_frames(found)
    return (found["bars"] is None and found["stars"] is None and len(picks) == 13
            and all(p["hud"] is None for p in picks))


@check
def the_star_row_is_found_and_text_is_not():
    """the scan finds the made-up star row without being told where it is: 5 slots (the number given), every star's
    middle within 2 px, the frames it calls 'stars' all truly show stars and the ones it calls 'no stars' never do;
    the line of changing text is not taken for the stars"""
    s = scanned()
    stars = s["found"]["stars"]
    if not stars:
        return False
    fit, facts = stars["fit"], s["facts"]
    mids = all(abs(fit["first_tip_x"] + k * fit["tip_spacing"] - (STAR["tip_x"] + k * STAR["spacing"])) <= 2.0
               for k in range(STAR["slots"]))
    mid_y = abs(fit["tip_y"] + fit["radius"] - (STAR["tip_y"] + STAR["r"])) <= 2.0
    box = fit["box"]
    off_text = box[1] + box[3] < TEXT_BOX[1]
    return (fit["slots"] == 5 and mids and mid_y and off_text and len(stars["yes"]) >= 4
            and all(facts[t]["stars"] for t in stars["yes"]) and not any(facts[t]["stars"] for t in stars["no"]))


@check
def the_outline_is_sized_so_the_stars_can_be_counted():
    """index.py's star count, read through the outline the scan hands to the page, counts stars on every picture
    that has them (an outline on the white middle's edge would count none) and none on a picture without. (How
    many it counts is not checked here: this made-up row draws a dark rim on its empty slots too, which GTA V's
    row does not.)"""
    import index
    s = scanned()
    fit = s["found"]["stars"]["fit"]
    prof = guided._star_prof(fit["box"], fit)
    masks, rule = index.star_masks(prof), dict(index.STAR_COUNT)
    read = read_area(s["full"])
    count = lambda t: index.count_stars(index.star_slots(read(t, fit["box"]), prof, masks), rule)
    return (all(count(t) >= 1 for t in s["found"]["stars"]["yes"])
            and all(count(t) == 0 for t in s["found"]["stars"]["no"]))


@check
def no_stars_in_a_recording_is_said_plainly():
    """the same HUD without a single star: the scan finds the bars, says there are no stars (None), every picture
    it picks is guessed 'stars: no', and the 3-minute check window starts where the HUD is on screen"""
    full, frames, facts = made_up_recording(seed=11, starred=0)
    found = guided.analyse(frames, FW // guided.DIV, FH // guided.DIV, (FW, FH), read_area(full), slots=5)
    picks = guided.choose_frames(found)
    start, length = guided.check_window(found, 405.0)
    return (found["bars"] is not None and found["stars"] is None and all(p["stars"] == "no" for p in picks)
            and length == 180.0 and any(start <= t < start + length for t, f in facts.items() if f["hud"]))


@check
def about_a_dozen_pictures_go_on_the_page():
    """the page gets at most 13 pictures: up to 4 with stars, 6 with the HUD and no stars, 3 with the HUD hidden;
    each guess is true for the made-up recording; and the 3-minute check window holds frames with stars"""
    s = scanned()
    picks = guided.choose_frames(s["found"])
    facts = s["facts"]
    kinds = [(p["hud"], p["stars"]) for p in picks]
    right = all((p["hud"] == "shown") == facts[p["t"]]["hud"] and (p["stars"] == "yes") == facts[p["t"]]["stars"]
                for p in picks)
    start, length = guided.check_window(s["found"], 405.0)
    return (len(picks) <= 13 and 2 <= kinds.count(("shown", "yes")) <= 4 and kinds.count(("shown", "no")) >= 3
            and 1 <= kinds.count(("hidden", "no")) <= 4 and right
            and any(start <= t < start + length for t in s["found"]["stars"]["yes"]))


# --- Guided calibration: PASS/FAIL and the one exception to never overwriting ---

def index_result(on=True, share=0.9, stars_s=30.0):
    return {"hud": {"on": on, "minimap_share": share, "stars_s": stars_s}}


@check
def the_verdict_is_decided_in_code():
    """PASS needs the minimap found and, when the scan saw stars in the checked part, stars found there and pictures
    on the star check page; a profile whose stars are still to do is 'STARS NOT CALIBRATED', never plain PASS; an
    index that didn't run, a lost minimap, missing stars or a rule saved with wrong pictures is FAIL"""
    v = guided.verdict
    return (v(index_result(), True, True, 6, [], [])[0] == "PASS"
            and v(index_result(stars_s=0.0), False, False, 0, [], ["stars", "phone"])[0]
            == "PASS for the minimap. STARS NOT CALIBRATED"
            and v(None, True, True, 0, [], [])[0] == "FAIL"
            and v(index_result(on=False, share=0.05), True, False, 0, [], [])[0] == "FAIL"
            and v(index_result(stars_s=0.0), True, True, 0, [], [])[0] == "FAIL"
            and v(index_result(), True, True, 0, [], [])[0] == "FAIL"
            and v(index_result(), True, True, 6, ["stars: saved although some labelled pictures come out wrong"], [])[0]
            == "FAIL"
            and v("skipped", False, False, 0, [], ["stars"])[0].startswith("SAVED, NOT CHECKED")
            and "STARS NOT CALIBRATED" in v("skipped", False, False, 0, [], ["stars"])[0])


def stand_in_owner(session, port, browser, guided=False):
    """What the owner does on the page, with every guess taken as it is: confirm, Measure, Save."""
    session.event("page_opened")
    for f in session.data["frames"]:
        for k in ("hud", "stars", "phone"):
            if f["labels"].get(k) is None and (f.get("suggest") or {}).get(k) is not None:
                f["labels"][k] = f["suggest"][k]
    fit = session.data["drawing"]["stars"].get("fit")
    if fit and not session.data["drawing"]["stars"].get("off"):
        fit["confirmed"] = True
    session.save_profile(True)
    return True


@check
def guided_run_on_a_recording_without_stars_and_the_rules_about_overwriting():
    """the whole guided command on a made-up video (bars and a phone, no stars) with a stand-in for the owner:
    it saves a profile clip.py loads, with both of the video's bars found by the scan (on this 12 s clip of pure
    noise a chance strip may be added beside them; the page is where the owner would delete it), stars off and
    marked 'not calibrated', the
    phone skipped; nothing was typed but the command. The same command again is allowed (the stars are still to
    do), keeps the earlier file in the session folder and never touches another profile; once the stars are done
    it refuses; held-out footage and a bad name are refused before anything is read"""
    import index
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        old = cal.CALIB_ROOT, clip.PROFILE_DIR, index.OFF_LIMITS
        cal.CALIB_ROOT, clip.PROFILE_DIR, index.OFF_LIMITS = tmp / "calib", tmp / "profiles", (tmp / "heldout",)
        said = []
        try:
            (tmp / "profiles").mkdir()
            (tmp / "heldout").mkdir()
            other = tmp / "profiles" / "other.json"
            other.write_text((HERE / "profiles" / "gta5.json").read_text(encoding="utf-8"), encoding="utf-8")
            video = tmp / "made_up.mp4"
            calibtest.made_up_video(video)
            barred = tmp / "heldout" / "secret.mp4"
            barred.write_bytes(b"not a video: it must never be opened")
            run = lambda name, v: guided.run(name, v, slots=5, skip_index=True, log=said.append, serve=stand_in_owner)
            first = run("made_up", video)
            saved = json.loads((tmp / "profiles" / "made_up.json").read_text(encoding="utf-8"))
            prof = clip.load_profile("made_up")
            ranges = [b["x_range"] for b in saved["minimap"]["bars"]]
            real_bars = all(any(a <= x0 + 6 and x1 - 6 <= b and b - a >= 30 for a, b in ranges)
                            for x0, x1 in ((20, 84), (90, 134)))        # where made_up_video draws its two bars
            first_ok = (first == 0 and not prof.stars_on and not prof.phone_on and real_bars
                        and saved["calibration"]["stars"] == "not calibrated"
                        and saved["calibration"]["guided"]["pending"] == ["stars", "phone"]
                        and saved["calibration"]["guided"]["bars_found_by_scan"] is True
                        and saved["calibration"]["bars"].startswith("found by the tool")
                        and guided.pending_parts("made_up") == ["stars", "phone"]
                        and any("STARS NOT CALIBRATED" in line for line in said))
            again = run("made_up", video)
            kept = list((tmp / "calib" / "made_up").glob("profile_before_*.json"))
            again_ok = again == 0 and len(kept) == 1 and other.read_text(encoding="utf-8") == (
                HERE / "profiles" / "gta5.json").read_text(encoding="utf-8")
            saved["calibration"]["guided"]["pending"] = ["phone"]       # as if the stars had been added
            (tmp / "profiles" / "made_up.json").write_text(json.dumps(saved), encoding="utf-8")
            done_text = (tmp / "profiles" / "made_up.json").read_text(encoding="utf-8")
            refused = (run("made_up", video) == 2 and run("other", video) == 2
                       and (tmp / "profiles" / "made_up.json").read_text(encoding="utf-8") == done_text
                       and run("fresh", barred) == 2 and run("Bad Name", video) == 2
                       and not (tmp / "calib" / "fresh").exists())
        finally:
            cal.CALIB_ROOT, clip.PROFILE_DIR, index.OFF_LIMITS = old
    return first_ok and again_ok and refused


@check
def a_finished_profile_is_never_written_again_by_hand_either():
    """calibrate.py's own Save still refuses a name that exists: only a guided run on a profile with stars to do
    sets the switch that lets Save finish it"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        old = cal.CALIB_ROOT, clip.PROFILE_DIR
        cal.CALIB_ROOT, clip.PROFILE_DIR = tmp / "calib", tmp / "profiles"
        try:
            (tmp / "profiles").mkdir()
            video = tmp / "made_up.mp4"
            calibtest.made_up_video(video)
            s = cal.Session("made_up")
            s.add(video, ["1", "2.5", "4", "7", "8", "11.5"])
            s.find_bars([10, 284, 140, 34])
            for f in s.data["frames"]:
                f["labels"] = {k: f["suggest"][k] for k in ("hud", "stars", "phone")}
            s.data["game"] = "Made Up"
            s.data["drawing"].update({"stars": {"off": True}, "phone": {"off": True, "grey": []}})
            switch_off = s.replace_ok is False
            s.save_profile(True)
            before = (tmp / "profiles" / "made_up.json").read_text(encoding="utf-8")
            refused = calibtest.raises(s.save_profile, True)
            same = (tmp / "profiles" / "made_up.json").read_text(encoding="utf-8") == before
        finally:
            cal.CALIB_ROOT, clip.PROFILE_DIR = old
    return switch_off and refused and same


# --- Library search: denied words, structured filters first, star rises ---

def seg(start, end, activity="driving", stars=0, notable=(), never=None, vehicle="car", police=False):
    return {"start_s": start, "end_s": end, "duration_s": end - start, "activity": activity,
            "never_use": activity in index.NEVER_USE if never is None else never, "max_stars": stars,
            "stars_s": float(end - start) if stars else 0.0, "vehicle": vehicle, "police_visible": police,
            "notable": [{"t": start, "text": t} for t in notable], "confidence": 0.9}


def made_up_index(stem, segments, duration=None, timeline=None):
    idx = {"video": {"stem": stem, "path": f"C:\\made_up\\{stem}.webm", "duration_s": duration or segments[-1]["end_s"]},
           "profile": {"name": "gta5"}, "settings": {"every_s": 6.0}, "segments": segments, "sheets": []}
    if timeline:
        idx["_timeline"] = timeline
    return idx


def made_up_timeline(counts, no_minimap=(), phone=()):
    """A HUD timeline from one star count per half second; no_minimap and phone are (first step, end step) ranges."""
    n = len(counts)
    flags = lambda ranges: [any(a <= i < b for a, b in ranges) for i in range(n)]
    gone, ph = flags(no_minimap), flags(phone)
    return {"on": True, "n": n, "minimap_share": 1.0, "stars": [c > 0 for c in counts], "count": list(counts),
            "phone": ph, "no_minimap": gone,
            "samples": [(i * clip.BUCKET, 0.6 if c else 0.0, not gone[i], ph[i], c) for i, c in enumerate(counts)]}


@check
def a_denied_word_is_not_a_match():
    """positive_text blanks what a line denies: the library's own lines 'no crashes or police', 'no vehicle or
    police', 'with no incident', 'no police visible' and 'without a crash' hold none of those words any more; "isn't
    chased" loses 'chased'; a comma doesn't end it ('no police, crashes or fire'); 'but' and a full stop do ('no
    police but a crash', 'No police. A car crashes.'); a line with no denial is unchanged apart from its case"""
    kept = library.positive_text
    gone = lambda line, *words: all(w not in kept(line) for w in words)
    return (gone("Player drives black car through city streets, no crashes or police.", "crash", "police")
            and "drives black car" in kept("Player drives black car through city streets, no crashes or police.")
            and gone("Man on phone, angry, in tropical setting, no vehicle or police", "vehicle", "police")
            and "phone" in kept("Man on phone, angry, in tropical setting, no vehicle or police")
            and gone("White truck drives through suburban streets with no incident.", "incident")
            and gone("Player drives truck near river under bridge, no police visible", "police")
            and gone("Player drives off without a crash", "crash")
            and gone("The player isn't chased by police", "chased", "police")
            and gone("no police, crashes or fire", "police", "crash", "fire")
            and "crash" in kept("no police but a crash") and "police" not in kept("no police but a crash")
            and "crashes" in kept("No police. A car crashes.") and "police" not in kept("No police. A car crashes.")
            and kept("Police car CRASHES into wall") == "police car crashes into wall"
            and len(kept("no crashes or police")) == len("no crashes or police"))


@check
def text_search_skips_denied_lines_and_says_so():
    """a text search for 'police' or 'crash' finds the segment whose line says it and not the one whose line denies
    it; the row counts the denying lines it passed over (text_denied); a segment with one line of each kind is found
    by its saying line only"""
    idx = made_up_index("a", [
        seg(0, 12, notable=["Player drives black car through city streets, no crashes or police."]),
        seg(12, 24, notable=["Black car crashes into police car."]),
        seg(24, 36, notable=["Calm drive, no police visible", "Police car follows the player"])])
    rows = library.find([idx], text=["police", "crash"])
    return ([r["segment"] for r in rows] == [2, 3] and rows[0]["text_denied"] == 0
            and [n["text"] for n in rows[1]["text_hits"]] == ["Police car follows the player"]
            and rows[1]["text_denied"] == 1
            and library.find([idx], text=["incident"]) == [])


@check
def structured_filters_run_before_the_text_search():
    """the text search reads only the segments the other filters leave: with activity, stars, length and never_use
    filters on, the lines of the segments those reject are never read (counted), and the answer is the same as
    filtering afterwards"""
    idx = made_up_index("a", [
        seg(0, 12, "driving", notable=["a crash"]), seg(12, 14, "chase", 2, notable=["a crash"]),
        seg(14, 30, "chase", 2, notable=["a crash"]), seg(30, 50, "cutscene", notable=["a crash"]),
        seg(50, 70, "chase", 1, notable=["a crash"]), seg(70, 90, "chase", 3, notable=["quiet road"])])
    read, real = [], library.segment_lines
    library.segment_lines = lambda i, s: read.append(s["start_s"]) or real(i, s)
    try:
        rows = library.find([idx], activity=["chase"], min_stars=2, min_len=6, text=["crash"])
    finally:
        library.segment_lines = real
    return [r["segment"] for r in rows] == [3] and read == [14, 70]


@check
def a_star_change_needs_a_steady_count_on_both_sides():
    """star_changes: 0,0,0,1,1,1 is one rise at the fourth step; a count that flickers for one half second (2,3,2)
    is no change; a rise straight after another unsteady step is skipped; falls are listed too, as falls"""
    c = library.star_changes
    return (c([0, 0, 0, 1, 1, 1]) == [(3, 0, 1)]
            and c([2, 2, 2, 3, 2, 2, 2]) == []
            and c([1, 1, 2, 2, 3, 3, 3]) == [(2, 1, 2), (4, 2, 3)]
            and c([0, 1, 0, 1, 0, 1, 1, 1]) == []
            and c([3, 3, 3, 0, 0, 0]) == [(3, 3, 0)]
            and c([]) == [] and c([1]) == [])


@check
def a_star_rise_window_starts_2_s_before_and_is_confirmed_at_both_ends():
    """rise_windows on a made-up recording: the rise from 1 to 3 stars at 20 s gives the window 18-22 s (2 s before,
    2 s after), with the HUD read 1 star at its start and 3 at its end, and it may span two segments (driving into
    chase); with after_s 5 the window ends 5 s after the change; a rise whose higher count drops again inside the
    window, one next to a cutscene, one where the minimap is gone and one at the very start of the recording are
    all left out; a fall is never a row"""
    counts = [0] * 20 + [1] * 20 + [3] * 30 + [0] * 10 + [2] * 3 + [0] * 17 + [1] * 20 + [0] * 10 + [2] * 20 + [0] * 10
    segs = [seg(0, 20, "driving"), seg(20, 35, "chase", 3), seg(35, 50, "on_foot"), seg(50, 58, "cutscene"),
            seg(58, 80, "on_foot", 2)]
    tl = made_up_timeline(counts, no_minimap=[(126, 132)])
    idx = made_up_index("a", segs, duration=80.0, timeline=tl)
    rows = library.rise_windows(idx, tl)
    longer = library.rise_windows(idx, tl, after_s=5.0)
    early = made_up_timeline([0] * 2 + [1] * 30)
    by_change = {r["change_s"]: r for r in rows}
    r = by_change.get(20.0)
    return (sorted(by_change) == [10.0, 20.0] and r is not None
            and (r["start_s"], r["end_s"], r["stars_before"], r["stars_after"]) == (18.0, 22.0, 1, 3)
            and r["hud_start"]["stars"] == 1 and r["hud_end"]["stars"] == 3 and r["segments"] == [1, 2]
            and r["holds_s"] == 15.0
            and [x["end_s"] for x in longer if x["change_s"] == 20.0] == [25.0]
            and library.rise_windows(made_up_index("b", [seg(0, 16)], timeline=early), early) == [])


@check
def stars_rise_is_a_query_like_any_other():
    """find(stars_rise=True) returns the rise windows with the video, its path and the segment's activity; min_stars
    and max_stars mean the count after the rise; activity and video filter as usual; a shot-list query may ask for
    it; a recording without saved HUD samples gives a plain error instead of an empty answer"""
    counts = [0] * 20 + [1] * 20 + [3] * 40
    tl = made_up_timeline(counts)
    idx = made_up_index("a", [seg(0, 20, "driving"), seg(20, 40, "chase", 3)], duration=40.0, timeline=tl)
    rows = library.find([idx], stars_rise=True)
    high = library.find([idx], stars_rise=True, min_stars=2)
    bare = made_up_index("c", [seg(0, 20)])
    try:
        library.find([bare], stars_rise=True)
        plain_error = False
    except library.LibraryError as e:
        plain_error = "hud.json" in str(e)
    return ([(r["stars_before"], r["stars_after"], r["activity"], r["video"]) for r in rows]
            == [(0, 1, "driving", "a"), (1, 3, "chase", "a")]
            and [r["stars_after"] for r in high] == [3]
            and library.find([idx], stars_rise=True, max_stars=1)[0]["stars_after"] == 1
            and library.find([idx], stars_rise=True, activity=["on_foot"]) == []
            and library.find([idx], stars_rise=True, video=["other"]) == []
            and library.check_query({"stars_rise": True, "min_stars": 2})["after_s"] == 2.0
            and plain_error)


@check
def the_command_line_prints_star_rises_as_json():
    """library.py find --stars-rise --json on a library folder made here (index.json, hud.json with made-up HUD
    samples, library.json): one rise, as JSON another tool can read, with the window and the HUD read at both ends;
    the plain find --json rows carry text_denied"""
    with tempfile.TemporaryDirectory() as tmp:
        lib = Path(tmp)
        counts = [0] * 20 + [2] * 40
        tl = made_up_timeline(counts)
        idx = made_up_index("rec", [seg(0, 10, "driving", notable=["Calm drive, no police"]),
                                    seg(10, 30, "chase", 2, notable=["Police chase the car"])], duration=30.0)
        idx.update(indexed="2026-11-14T00:00:00", model={"name": "made-up"})
        (lib / "rec").mkdir()
        (lib / "rec" / "index.json").write_text(json.dumps(idx), encoding="utf-8")
        (lib / "rec" / "hud.json").write_text(json.dumps({"fields": {}, "frame_times": [], "samples": tl["samples"]}),
                                              encoding="utf-8")
        (lib / "library.json").write_text(json.dumps({"version": 1, "videos": {"rec": {"index": "rec/index.json"}}}),
                                          encoding="utf-8")
        run = lambda *args: subprocess.run([sys.executable, str(HERE / "library.py"), "--library", str(lib), "find",
                                            *args, "--json"], capture_output=True, text=True, cwd=HERE)
        rises, plain = run("--stars-rise"), run("--text", "police")
        if rises.returncode or plain.returncode:
            return False
        r, rows = json.loads(rises.stdout), json.loads(plain.stdout)
    return (len(r) == 1 and (r[0]["start_s"], r[0]["end_s"], r[0]["stars_before"], r[0]["stars_after"])
            == (8.0, 12.0, 0, 2) and r[0]["hud_start"]["stars"] == 0 and r[0]["hud_end"]["stars"] == 2
            and r[0]["video"] == "rec" and [x["segment"] for x in rows] == [2] and rows[0]["text_denied"] == 0)


# --- Mode B: Shorts built from the owner's own footage (footage_short.py) ---

def made_up_library():
    """Three made-up recordings. alpha: a calm drive, a cutscene, then a chase where the stars go 0 -> 1 at 50 s and
    1 -> 3 at 60 s. bravo: a long calm drive, then a shootout. charlie: 4 s on foot (too short for much)."""
    alpha = made_up_index("alpha", [seg(0, 30, "driving", notable=["Player drives a car down a street."]),
                                    seg(30, 40, "cutscene"),
                                    seg(40, 100, "chase", 3, police=True, notable=["Car crashes into police car."])],
                          timeline=made_up_timeline([0] * 100 + [1] * 20 + [3] * 80, no_minimap=[(60, 80)]))
    alpha["segments"][2]["notable"][0]["t"] = 60.0
    bravo = made_up_index("bravo", [seg(0, 60, "driving", notable=["Truck drives on a highway."]),
                                    seg(60, 90, "shootout", notable=["Player aims gun at bikers."])],
                          timeline=made_up_timeline([0] * 180))
    charlie = made_up_index("charlie", [seg(0, 4, "on_foot", notable=["Player walks."])],
                            timeline=made_up_timeline([0] * 8))
    return [alpha, bravo, charlie]


def row(indexes, **query):
    return library.find(indexes, **query)[0]


@check
def footage_is_cut_on_clean_edges():
    """place() on a made-up recording: a 3 s beat in the chase sits at its middle, half a second clear of the
    cutscene before it and a second clear of both star changes; with the query's word seen at 60 s it moves to that
    moment instead, still a second after the change; footage another beat shows is left alone; a beat that needs
    1.5 s of clean lead-in (the loop ending) or a longer clean tail (a text card blurred over it) is refused where
    the footage is too short for it"""
    lib = made_up_library()
    alpha, charlie = lib[0], lib[2]
    chase = row(lib, video=["alpha"], activity=["chase"])
    crash = row(lib, video=["alpha"], activity=["chase"], text=["crash"])
    walk = row(lib, video=["charlie"])
    place = footage_short.place
    moved = place(alpha, chase, 3.0, taken=[("alpha", 64.0, 80.0)])
    return (place(alpha, chase, 3.0) == 68.5
            and place(alpha, crash, 3.0) == 61.5
            and moved is not None and (moved + 3.0 <= 63.0 or moved >= 81.0)
            and all(abs(c - moved) >= 1.0 and abs(c - moved - 3.0) >= 1.0 for c in (50.0, 60.0))
            and place(alpha, chase, 3.0, lead=1.5) - 1.5 >= 40.5
            and place(charlie, walk, 3.0) == 0.5
            and place(charlie, walk, 3.0, lead=1.5) is None
            and place(charlie, walk, 3.0, tail=5.0) is None
            and place(charlie, walk, 6.0) is None)


@check
def a_star_rise_beat_keeps_the_change_inside_it():
    """a beat filled from a star rise starts 2 s before the change when it is long enough (4 s: 58-62 s for the
    rise at 60 s), and earlier in the beat when it is short (2 s: the change 1 s in), so the lower count is on screen
    first and the higher one after; the 0 -> 1 rise at 50 s is cut the same way; a rise whose lead-in would reach
    into the cutscene is refused"""
    lib = made_up_library()
    alpha = lib[0]
    rises = {r["change_s"]: r for r in library.find(lib, stars_rise=True, video=["alpha"])}
    place = footage_short.place
    near_cutscene = dict(rises[60.0], change_s=41.5, holds_s=8.0)
    return (sorted(rises) == [50.0, 60.0]
            and place(alpha, rises[60.0], 4.0) == 58.0 and place(alpha, rises[60.0], 2.0) == 59.0
            and place(alpha, rises[50.0], 4.0) == 48.0
            and place(alpha, near_cutscene, 4.0) is None)


@check
def the_best_unused_footage_is_picked():
    """pick_beats on the made-up library: a beat about wanted stars gets the star rise (1 -> 3), not just any
    stretch with stars; the next beat prefers a recording the Short hasn't used; asked for the same thing again it
    takes another segment, not the same one; with no activity named the chase beats the calm drive (more action);
    a beat nothing matches says so; and no two beats show the same footage"""
    lib = made_up_library()
    beats = [{"line": 0, "shot": 0, "query": {"activity": ["chase"], "min_stars": 2}, "seconds": 3.0, "lead": 1.5},
             {"line": 0, "shot": 1, "query": {"activity": ["driving"]}, "seconds": 2.0},
             {"line": 1, "shot": 0, "query": {"activity": ["driving"]}, "seconds": 2.0},
             {"line": 1, "shot": 1, "query": {"activity": ["shootout"]}, "seconds": 2.0},
             {"line": 2, "shot": 0, "query": {"activity": ["boat"]}, "seconds": 2.0}]
    picks = footage_short.pick_beats(beats, lib)
    any_action = footage_short.pick_beats([{"line": 0, "shot": 0, "query": {"min_len": 6}, "seconds": 2.0}], lib)[0]
    spans = [(p["video"], p["start_s"], p["end_s"]) for p in picks if p["found"]]
    apart = all(a[0] != b[0] or a[2] <= b[1] or b[2] <= a[1] for k, a in enumerate(spans) for b in spans[k + 1:])
    return (picks[0]["found"] and picks[0]["kind"] == "rise" and picks[0]["video"] == "alpha"
            and picks[0]["start_s"] < 60.0 < picks[0]["end_s"] and "from 1 to 3" in picks[0]["why"]
            and (picks[1]["video"], picks[1]["activity"]) == ("bravo", "driving")
            and (picks[2]["video"], picks[2]["activity"]) == ("alpha", "driving")
            and picks[3]["video"] == "bravo" and picks[3]["activity"] == "shootout"
            and picks[4]["found"] is False and "no footage" in picks[4]["why"]
            and apart and any_action["activity"] == "chase")


@check
def footage_an_earlier_short_showed_is_avoided_while_there_is_other():
    """with bravo's drive shown by an earlier Short, a driving beat takes alpha's drive instead; with both shown it
    takes one again rather than nothing, and the pick says it was reused"""
    lib = made_up_library()
    beat = [{"line": 0, "shot": 0, "query": {"activity": ["driving"]}, "seconds": 2.0}]
    fresh = footage_short.pick_beats(beat, lib, avoid=[("bravo", 0.0, 60.0)])[0]
    again = footage_short.pick_beats(beat, lib, avoid=[("bravo", 0.0, 60.0), ("alpha", 0.0, 30.0)])[0]
    return (fresh["video"] == "alpha" and fresh["reused"] is False
            and again["found"] and again["reused"] is True and "earlier Short" in again["why"])


FOOTAGE_TITLE = "My LAST ride in GTA 5 before GTA 6 lands"


def footage_script(**changes):
    script = {
        "title": FOOTAGE_TITLE, "description": "One last ride through my own GTA 5 footage.",
        "hashtags": ["#GTA5", "#GTA6", "#Shorts"], "voice": "am_puck", "speed": 1.0, "music": None, "sfx": True,
        "footage": {"game_audio": True, "profile": "gta5"},
        "lines": [
            {"say": "This is my last ride in GTA 5, and it starts with the police right behind me.", "facts": [],
             "beats": [{"beat": "full", "shot": {"activity": ["chase"], "min_stars": 2}},
                       {"beat": "card", "card_aspect": "16:9", "shot": {"activity": ["driving"], "max_stars": 0}}]},
            {"say": "Then a calm drive, a shootout, and a boat that I never recorded at all.", "facts": [],
             "beats": [{"beat": "full", "shot": {"activity": ["driving"], "max_stars": 0}},
                       {"beat": "banner", "text": "Shootout", "shot": {"activity": ["shootout"]}},
                       {"beat": "full", "shot": {"activity": ["boat"]}, "query": "airboat swamp"}]},
            {"say": "After all these years the old streets still feel like home to me, every single one.", "facts": [],
             "beats": [{"beat": "card", "card": "ONE|LAST|RIDE"},
                       {"beat": "full", "shot": {"activity": ["chase"], "stars_rise": False}}]},
            {"say": "So this is goodbye for now, and thank you for riding along with me today.", "facts": [],
             "beats": [{"beat": "full", "shot": {"activity": ["driving"]}}]}]}
    script.update(changes)
    return script


def stand_in_timing(path, root):
    """Every line 5.5 s, shared equally by its beats; the loop ending repeats the first beat for 1.5 s; a text card
    blurs the next picture (else the one before), as explainer.py does."""
    script = json.loads(Path(path).read_text(encoding="utf-8"))
    lines = [[5.5 / len(line["beats"])] * len(line["beats"]) for line in script["lines"]]
    order = [(n, i, b) for n, line in enumerate(script["lines"]) for i, b in enumerate(line["beats"])]
    pictures = [k for k, (_, _, b) in enumerate(order) if "file" in b]
    behind = {}
    for k, (n, i, b) in enumerate(order):
        if "card" in b:
            after = [j for j in pictures if j > k]
            j = after[0] if after else [j for j in pictures if j < k][-1]
            behind[(n, i)] = order[j][:2]
    STATE["timing_script"] = script
    return {"total_s": 5.5 * len(lines), "lines": lines, "loop_s": 1.5, "first": (0, 0), "behind": behind}


def stand_in_render(shift=0.0):
    """A render that writes the log explainer.py would: every footage beat, the footage behind each text card and
    the loop ending, each with where in its source it starts. shift moves the first beat's footage (a render gone
    wrong, for the check after it)."""
    def render(resolved_path, out, root, shorts_dir):
        script = json.loads(Path(resolved_path).read_text(encoding="utf-8"))
        times = stand_in_timing(resolved_path, root)
        visuals, first = [], True
        for n, line in enumerate(script["lines"]):
            for i, b in enumerate(line["beats"]):
                seconds = times["lines"][n][i]
                if "card" in b:
                    pn, pi = times["behind"][(n, i)]
                    bg = script["lines"][pn]["beats"][pi]
                    visuals.append({"line": n + 1, "source": "card:" + b["card"], "kind": "card", "seconds": seconds,
                                    "behind": f"{bg['file']}@{bg.get('at', 0)}", "loop_tail": False,
                                    "source_start_s": float(bg.get("at", 0)) if "at" in bg else None})
                    continue
                at = float(b.get("at", 0)) + (shift if first else 0.0)
                video = "at" in b
                visuals.append({"line": n + 1, "source": f"{b['file']}@{b.get('at', 0)}", "kind": "video" if video else "image",
                                "seconds": seconds, "behind": None, "loop_tail": False, "sound": bool(b.get("sound")),
                                "source_start_s": at if video else None})
                if first:
                    tail = dict(visuals[-1], line=len(script["lines"]), seconds=1.5, loop_tail=True,
                                source_start_s=at - 1.5)
                first = False
        visuals.append(tail)
        (Path(out) / "log.json").write_text(json.dumps({"visuals": visuals, "warnings": [], "game_audio": None,
                                                        "pacing": {"length_s": times["total_s"]}}), encoding="utf-8")
        (Path(out) / "short.mp4").write_bytes(b"")
        return lambda pacing: [f"  Length        {pacing['length_s']:.1f} s"]
    return render


def footage_run(tmp, script=None, name="script.json", render=None, **more):
    path = Path(tmp) / name
    path.write_text(json.dumps(script or footage_script()), encoding="utf-8")
    said, thumbs = [], []
    out, ready = footage_short.footage_short(
        path, shorts_dir=Path(tmp) / "shorts", today="2026-11-14", facts=shorttest.FACTS, tags=shorttest.fake_tags(),
        indexes=more.pop("indexes", None) or made_up_library(), timing=stand_in_timing,
        render=render or stand_in_render(), log=said.append,
        thumb=lambda source, t, out: thumbs.append((str(source), t)) or False, **more)
    return out, ready, said, thumbs


@check
def a_footage_short_is_built_from_the_library_end_to_end():
    """the whole of footage_short.py on a made-up library, with stand-ins for the voice timing and the render: every
    "shot" beat becomes a file with a start time and "sound": true in the script explainer.py gets (which holds no
    "shot", "footage" or "facts" any more); the first beat keeps 1.5 s of clean lead-in for the loop ending and the
    beat a text card blurs keeps a clean tail; the beat with no footage falls back to the Rockstar picture its
    "query" asks for and picks.json says so; picks.json has one row per beat with video, start, end and why;
    picks.html is written with a thumbnail asked for per picture; the Short is READY and its footage is logged as
    used"""
    with tempfile.TemporaryDirectory() as tmp:
        out, ready, said, thumbs = footage_run(tmp)
        resolved = json.loads((out / "script_resolved.json").read_text(encoding="utf-8"))
        picks = json.loads((out / "picks.json").read_text(encoding="utf-8"))
        page = (out / "picks.html").read_text(encoding="utf-8")
        used = json.loads((Path(tmp) / "shorts" / footage_short.USED_FOOTAGE).read_text(encoding="utf-8"))
        files = {p.name for p in out.iterdir()}
    beats = [b for line in resolved["lines"] for b in line["beats"]]
    footage = [b for b in beats if "at" in b]
    rows = picks["beats"]
    fallback = [r for r in rows if r["source"] == "rockstar fallback"]
    first = rows[0]
    return (ready and "footage" not in resolved and all("facts" not in line for line in resolved["lines"])
            and all("shot" not in b for b in beats) and len(footage) == 6 and all(b.get("sound") is True for b in footage)
            and len(rows) == 8 and picks["beats_from_footage"] == 6 and picks["beats_fell_back_to_rockstar"] == 1
            and first["source"] == "footage" and first["video"] == "alpha" and first["kind"] == "rise"
            and first["start_s"] - 1.5 >= 40.5 and first["end_s"] - first["start_s"] == 2.75
            and len(fallback) == 1 and fallback[0]["line"] == 2 and "no footage" in fallback[0]["why"]
            and fallback[0]["path"] == "rockstar/" + shorttest.AIRBOAT
            and any("NO FOOTAGE" in line for line in said) and any(line.startswith("READY") for line in said)
            and all(r.get("why") for r in rows) and picks["ready"] is True
            and all(s["what"] in ("a beat", "behind a text card", "the loop ending") for s in picks["shown"])
            and {"script.json", "script_resolved.json", "picks.json", "picks.html", "upload.txt", "claims.txt",
                 "pacing.txt", "short.mp4"} <= files and "script_timing.json" not in files
            and page.count("<article") == 8 and "ROCKSTAR FALLBACK" in page and len(thumbs) == 7
            and used["shorts"][0]["name"].endswith("my-last-ride-in-gta-5-before-gta-6-lands")
            and len(used["shorts"][0]["spans"]) == len(picks["shown"]))


@check
def never_use_footage_never_gets_on_screen():
    """no footage the Short shows touches the made-up cutscene (alpha 30-40 s), not a beat, not the loop ending and
    not the footage behind the text card; and when a render does put a cutscene on screen (the stand-in shifts the
    first beat's footage back into it), the check after the render says so and the Short is NOT READY and is not
    logged as used"""
    with tempfile.TemporaryDirectory() as tmp:
        out, ready, _, _ = footage_run(tmp)
        shown = json.loads((out / "picks.json").read_text(encoding="utf-8"))["shown"]
        clear = all(not (Path(s["video"]).stem == "alpha" and s["start_s"] < 40.0 and s["end_s"] > 30.0) for s in shown)
    with tempfile.TemporaryDirectory() as tmp:
        out, bad_ready, said, _ = footage_run(tmp, render=stand_in_render(shift=-25.0))
        logged = (Path(tmp) / "shorts" / footage_short.USED_FOOTAGE).exists()
    return (ready and clear and len(shown) >= 8 and bad_ready is False and not logged
            and any(line.startswith("NOT READY") and "not all usable footage" in line for line in said))


@check
def a_second_short_does_not_repeat_the_first_ones_footage():
    """two Shorts made one after the other from the same library: where the library has other footage for a beat,
    the second Short takes it (its calm drives start somewhere else); rendering the first one again keeps its own
    picks"""
    with tempfile.TemporaryDirectory() as tmp:
        one, _, _, _ = footage_run(tmp)
        first = json.loads((one / "picks.json").read_text(encoding="utf-8"))["beats"]
        two, _, _, _ = footage_run(tmp, footage_script(title="One MORE ride in GTA 5 before GTA 6"), "two.json")
        second = json.loads((two / "picks.json").read_text(encoding="utf-8"))["beats"]
        again, _, _, _ = footage_run(tmp)
        repeat = json.loads((again / "picks.json").read_text(encoding="utf-8"))["beats"]
    span = lambda r: (r["video"], r["start_s"], r["end_s"])
    calm = lambda rows: {span(r) for r in rows if r["source"] == "footage" and r["activity"] == "driving"}
    return (one != two and again == one and calm(first) and not (calm(first) & calm(second))
            and [span(r) for r in repeat if r["source"] == "footage"]
            == [span(r) for r in first if r["source"] == "footage"])


@check
def a_script_that_cannot_be_a_footage_short_is_refused_with_reasons():
    """before anything is picked or rendered: a beat with both a shot and a file, footage in a beat type that can't
    hold it, a shot-list id that doesn't exist, an unknown query key, a script with no shot at all, too few words,
    and a library with no recording for the profile asked for are each refused with a plain reason"""
    def refused(script, *words, **more):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                footage_run(tmp, script, **more)
            except footage_short.FootageError as e:
                return all(w in str(e) for w in words)
        return False
    beats = lambda *b: footage_script(lines=[dict(footage_script()["lines"][0], beats=list(b)),
                                             *footage_script()["lines"][1:]])
    no_shots = footage_script(lines=[dict(line, beats=[{"beat": "card", "card": "HELLO"}])
                                     for line in footage_script()["lines"]])
    short = footage_script(lines=[dict(footage_script()["lines"][0], say="Too short.")])
    return (refused(beats({"beat": "full", "shot": {"activity": ["chase"]}, "file": "rockstar/x.jpg"}), "one of them")
            and refused(beats({"beat": "lockon", "shot": {"activity": ["chase"]}}), "full, card or banner")
            and refused(beats({"beat": "full", "shot": "police-chase/no-such-shot"}), "not in shotlist.json")
            and refused(beats({"beat": "full", "shot": {"speed": "fast"}}), "unknown query key")
            and refused(no_shots, "no beat asks for footage")
            and refused(short, "spoken words")
            and refused(footage_script(footage={"profile": "gta6"}), "no recording", "gta6"))


@check
def shot_list_ids_and_fallback_words():
    """a beat's shot may be a shot-list id ("police-chase/long-chase" is that shot's query); a beat with no "query"
    of its own falls back to plain words for what it was after (a chase: police car); its own "query" wins; and a
    star-rise query never takes the two length filters with it, which are about segments"""
    query = footage_short.shot_query("police-chase/long-chase")
    lib = made_up_library()
    rises = footage_short.candidates(lib, {"stars_rise": True, "min_len": 500})
    return (query == {"activity": ["chase"], "min_stars": 1, "min_len": 15}
            and footage_short.fallback_query({"shot": {"activity": ["chase"]}}) == "police car"
            and footage_short.fallback_query({"shot": {"activity": ["boat"]}, "query": "airboat swamp"}) == "airboat swamp"
            and len(rises) == 2 and all("change_s" in r for r in rises))


def explainer_or_none():
    """explainer.py, when this is run with .venv-tts (it needs numpy and Kokoro); None with plain .venv."""
    try:
        import numpy  # noqa: F401
        import explainer
        return explainer
    except ImportError:
        return None


def tone_video(path, seconds=6, hz=440):
    """A small video with a steady tone as its sound."""
    subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s=640x360:r=30:d={seconds}", "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={seconds}",
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                   check=True)


@check
def explainer_reads_sound_on_a_video_shot():
    """(needs .venv-tts; SKIPPED with plain .venv) explainer.py takes "sound": true on a video shot and nowhere
    else: an image with sound, a text card with sound and a sound that isn't true or false are refused; a script
    without it reads as before, with game_volume 1; a game_volume out of range is refused"""
    explainer = explainer_or_none()
    if explainer is None:
        return "SKIP"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        tone_video(root / "clip.mp4")
        subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=gray:s=640x360", "-frames:v", "1", str(root / "still.jpg")], check=True)

        def read(*beats, **top):
            path = root / "s.json"
            path.write_text(json.dumps({"title": "Sound test", "lines": [{"say": "One line here.", "beats": list(beats)}],
                                        **top}), encoding="utf-8")
            return explainer.load_script(path, root)

        def refused(*beats, **top):
            try:
                read(*beats, **top)
            except explainer.ScriptError:
                return True
            return False
        with_sound = read({"beat": "full", "file": "clip.mp4", "at": "1", "sound": True})
        plain = read({"beat": "full", "file": "clip.mp4", "at": "1"})
        return (with_sound["lines"][0]["visuals"][0].get("sound") is True
                and "sound" not in plain["lines"][0]["visuals"][0] and plain["game_volume"] == 1.0
                and read({"beat": "full", "file": "clip.mp4"}, game_volume=0.5)["game_volume"] == 0.5
                and refused({"beat": "full", "file": "still.jpg", "sound": True})
                and refused({"beat": "card", "card": "HELLO", "sound": True}, {"beat": "full", "file": "clip.mp4"})
                and refused({"beat": "full", "file": "clip.mp4", "sound": "yes"})
                and refused({"beat": "full", "file": "clip.mp4"}, game_volume=9))


@check
def game_sound_sits_under_the_voice_only_where_its_shot_is():
    """(needs .venv-tts; SKIPPED with plain .venv) explainer.game_bed with a made-up voice: the footage's sound is
    there only during the shot that asked for it (silent before and after, silent for a shot without "sound" and
    for the loop ending's repeat), it fades in and out at the cuts, and while the voice speaks it sits about 13 dB
    under it (within 2 dB)"""
    explainer = explainer_or_none()
    if explainer is None:
        return "SKIP"
    import numpy as np
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        tone_video(work / "clip.mp4")
        rate, fps = explainer.RATE, explainer.FPS
        t = np.arange(6 * rate) / rate
        voice = np.repeat((0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)[:, None], 2, axis=1)
        video = {"kind": "video", "path": work / "clip.mp4", "start": 1.0, "source": "clip.mp4@1", "sound": True}
        quiet = dict(video, sound=False)
        slots = [{"line": 0, "start": 0, "frames": 2 * fps, "visual": quiet, "start_s": 1.0},
                 {"line": 0, "start": 2 * fps, "frames": 2 * fps, "visual": video, "start_s": 1.0},
                 {"line": 0, "start": 4 * fps, "frames": 2 * fps, "visual": video, "start_s": 1.0, "loop_tail": True}]
        bed, info = explainer.game_bed(slots, voice, 1.0, work)
        none, _ = explainer.game_bed(slots[:1], voice, 1.0, work)
    rms = lambda a, b, x=None: float(np.sqrt(((bed if x is None else x)[int(a * rate):int(b * rate)] ** 2).mean()))
    under = 20 * np.log10(rms(2.5, 3.5, voice) / rms(2.5, 3.5))
    return (none is None and len(info["game_pieces"]) == 1 and rms(0.0, 1.9) == 0.0 and rms(4.1, 6.0) == 0.0
            and rms(2.5, 3.5) > 0.0 and rms(2.0, 2.005) < 0.5 * rms(2.5, 3.5) and 11.0 <= under <= 15.0)


def main():
    ok, skipped = True, 0
    for fn in CHECKS:
        try:
            got = fn()
            good, err = bool(got), ""
        except Exception as e:  # a missing function or field is a failure of this check, not of the run
            got, good, err = None, False, f"  ({type(e).__name__}: {e})"
        ok &= good
        skipped += got == "SKIP"
        word = "SKIP" if got == "SKIP" else "PASS" if good else "FAIL"
        print(f"  {word} {fn.__name__}: {' '.join((fn.__doc__ or '').split())}{err}", flush=True)
    if skipped:
        print(f"{skipped} check(s) SKIPPED: they need explainer.py's own Python. Run this file with "
              ".venv-tts\\Scripts\\python.exe to include them.")
    print("RESULT: " + ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
