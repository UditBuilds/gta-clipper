"""Checks for PR 22, session 2: the guided calibration (calibrate.py --guided, calibrate_guided.py), the library
search fixes (library.py) and Shorts built from the owner's own footage (footage_short.py).

No Ollama, no Kokoro voice and none of the owner's footage are used: the HUDs, the recordings, the model's answers
and the voice's timings are all made up here. The guided-calibration checks draw their own HUD (three coloured bars,
a row of five stars, a line of changing text) at a place and size no profile has, so nothing of GTA V's layout can
leak into what the scan finds.

Each check prints PASS or FAIL with what it tests. A check that raises counts as FAIL (with the error), so a missing
function shows as a failure, not a crash. The last line is RESULT: PASS or FAIL; exit code 0 or 1.

Usage:  python footagetest.py
"""

import json
import random
import sys
import tempfile
from pathlib import Path

import calibrate as cal
import calibrate_guided as guided
import calibtest
import clip

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


def main():
    ok = True
    for fn in CHECKS:
        try:
            good, err = bool(fn()), ""
        except Exception as e:  # a missing function or field is a failure of this check, not of the run
            good, err = False, f"  ({type(e).__name__}: {e})"
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'} {fn.__name__}: {' '.join((fn.__doc__ or '').split())}{err}", flush=True)
    print("RESULT: " + ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
