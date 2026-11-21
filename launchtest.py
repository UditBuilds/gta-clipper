"""Checks for /launch (PR 25): launch.py, safety.py and truth.py. No Ollama and no real footage: the indexes, the
vision model's answers and the guided calibration are made up here (the made-up index helpers are footagetest.py's).
ffmpeg is used where a check needs a real file (a frame sheet, a true-peak measurement).

  .venv\\Scripts\\python.exe launchtest.py
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import calibrate as cal
import clip
import footage_short
import index
import launch
import safety
import truth
import truth
from footagetest import made_up_index, made_up_timeline, seg

HERE = Path(__file__).resolve().parent
CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


# --- A made-up 600 s recording ---
# 0-100 driving, 100-160 chase (stars 0 -> 1 at 110, 1 -> 2 at 120), 160-200 cutscene, 200-400 on foot,
# 400-430 shootout (a car explodes at about 414), 430-600 driving.

DURATION = 600.0


def made_up_counts():
    return [0 if i * 0.5 < 110 else 1 if i * 0.5 < 120 else 2 if i * 0.5 < 160 else 0 for i in range(int(DURATION * 2))]


def made_up_sheets():
    sheets = []
    for k in range(int(DURATION // 6)):
        t = k * 6.0
        text, activity, police = "Player drives down a city street", "driving", False
        if 100 <= t < 160:
            text, activity, police = "Car speeds away from a police car", "chase", True
        if t == 132.0:
            text = "Police car crashes into a wall behind the player"
        if 160 <= t < 200:
            text, activity = "Close-up of two characters talking in a room", "cutscene"
        if 200 <= t < 400:
            text, activity = "Player walks along a beach, no police or crashes", "on_foot"
        if 400 <= t < 430:
            text, activity = "Player shoots at gang members", "shootout"
        if t == 408.0:
            text = "Player shoots and a car explodes in a fireball"
        sheets.append({"t": t, "file": f"frames/{index.frame_name(t)}", "error": None, "notable": text,
                       "activity": activity, "vehicle": "car", "police_visible": police, "confidence": 0.9,
                       "hud_stars": 0})
    return sheets


def made_up_idx(stem="rec_a", path=None):
    segs = [seg(0, 100, "driving"), seg(100, 160, "chase", stars=2, police=True), seg(160, 200, "cutscene"),
            seg(200, 400, "on_foot"), seg(400, 430, "shootout"), seg(430, 600, "driving")]
    for s in segs:
        s["decided_by"] = "model"
    idx = made_up_index(stem, segs, DURATION, made_up_timeline(made_up_counts(), no_minimap=((320, 400),)))
    idx["sheets"] = made_up_sheets()
    if path:
        idx["video"]["path"] = str(path)
    return idx


def made_up_act(peaks=((415.0, 5.0), (130.0, 3.0))):
    act = [0.0] * int(DURATION * 2)
    for t, v in peaks:
        for i in range(int((t - 3) * 2), int((t + 3) * 2)):
            act[i] = v
    return act


def made_up_rec(mode="hud", offset=0.0, stem="rec_a"):
    idx = made_up_idx(stem)
    if mode == "nohud":
        idx.pop("_timeline")
    return launch.recording_data(stem, idx, made_up_act(), mode, offset)


# --- 1. Recordings, run folders, the profile ---

@check
def ps5_names_give_the_session_order():
    """the time in a PS5 file name orders the recordings; a name without one falls back to the file's time"""
    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp, "Grand Theft Auto VI_20261119013000.webm")
        b = Path(tmp, "Grand Theft Auto VI_20261119001500.webm")
        c = Path(tmp, "my clip.webm")
        for p in (a, b, c):
            p.write_bytes(b"x")
        return (launch.recording_time(a).hour == 1 and launch.recording_time(b) < launch.recording_time(a)
                and launch.recording_time(c).year >= 2026)


@check
def only_new_recordings_are_taken_and_held_out_never():
    """a recording already in the library or taken by an earlier run is skipped with the reason; a run that stopped
    while preparing gets its own recordings back; held-out footage is refused even when named"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        inp, lib, held = tmp / "input", tmp / "library", tmp / "heldout"
        for d in (inp, lib, held):
            d.mkdir()
        for name in ("old.webm", "part1_20261119000000.webm", "part2_20261119010000.webm", "notes.txt"):
            (inp / name).write_bytes(b"x")
        (held / "secret.webm").write_bytes(b"x")
        (lib / "library.json").write_text(json.dumps({"videos": {"old": {}}}), encoding="utf-8")
        old_limits = index.OFF_LIMITS
        index.OFF_LIMITS = (held,)
        try:
            runs = {"runs": [{"folder": str(tmp / "launch-x"), "recordings": [str(inp / "part1_20261119000000.webm")],
                              "status": "prepared"}]}
            new, skipped = launch.find_recordings(inp, lib, runs)
            resumed, _ = launch.find_recordings(inp, lib, {"runs": [dict(runs["runs"][0], status="preparing")]})
            named, why = launch.find_recordings(inp, lib, {"runs": []}, [str(held / "secret.webm")])
        finally:
            index.OFF_LIMITS = old_limits
        return ([v.name for v in new] == ["part2_20261119010000.webm"]
                and "library" in skipped["old.webm"] and "earlier" in skipped["part1_20261119000000.webm"]
                and [v.name for v in resumed] == ["part1_20261119000000.webm"]
                and named == [] and "held-out" in why["secret.webm"])


@check
def a_second_run_the_same_night_gets_its_own_folder():
    """launch-<day> first, then launch-<day>-part2; a run that stopped is carried on in its folder; a dry run has
    its own; note_run records the run and never writes in a dry run"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        first = launch.run_folder(tmp, "2026-11-19", {"runs": []})
        launch.note_run(tmp, first, [tmp / "a.webm"], "prepared")
        second = launch.run_folder(tmp, "2026-11-19", launch.load_runs(tmp))
        launch.note_run(tmp, second, [tmp / "b.webm"], "preparing")
        again = launch.run_folder(tmp, "2026-11-19", launch.load_runs(tmp))
        dry = launch.run_folder(tmp, "2026-11-19", launch.load_runs(tmp), dry_run=True)
        launch.note_run(tmp, dry, [tmp / "c.webm"], "prepared", dry_run=True)
        runs = launch.load_runs(tmp)["runs"]
        return (first.name == "launch-2026-11-19" and second.name == "launch-2026-11-19-part2" and again == second
                and dry.name == "launch-2026-11-19-dryrun" and len(runs) == 2
                and [r["status"] for r in runs] == ["prepared", "preparing"])


def stand_in_guided(results, saves=True):
    """A guided calibration that ends with the given results in turn ("PASS", "PASS for the minimap. STARS NOT
    CALIBRATED", "FAIL", or None for a run that can't start), saving a profile as the real one does."""
    calls = []

    def run(name, video, slots=None, game=None, browser=True, log=print, serve=None):
        result = results[len(calls)]
        calls.append((name, [Path(v).name for v in video] if isinstance(video, list) else Path(video).name, slots,
                       serve))
        if result is None:
            log("ERROR: video not found: made up")
            return 2
        if saves:
            prof = json.loads((HERE / "profiles" / "gta5.json").read_text(encoding="utf-8"))
            prof["name"] = name
            prof["stars"]["on"] = "STARS NOT" not in result
            prof["calibration"] = {"guided": {"pending": ["phone"] + (["stars"] if "STARS NOT" in result else [])}}
            (clip.PROFILE_DIR / f"{name}.json").write_text(json.dumps(prof), encoding="utf-8")
        (cal.CALIB_ROOT / name).mkdir(parents=True, exist_ok=True)
        (cal.CALIB_ROOT / name / "guided_result.json").write_text(json.dumps({"result": result}), encoding="utf-8")
        return 1 if result == "FAIL" else 0
    return run, calls


def with_profile_dirs(fn):
    with tempfile.TemporaryDirectory() as tmp:
        old = clip.PROFILE_DIR, cal.CALIB_ROOT
        clip.PROFILE_DIR, cal.CALIB_ROOT = Path(tmp) / "profiles", Path(tmp) / "calib"
        clip.PROFILE_DIR.mkdir()
        for f in (HERE / "profiles").glob("*_nohud.json"):
            shutil.copyfile(f, clip.PROFILE_DIR / f.name)
        try:
            return fn(Path(tmp))
        finally:
            clip.PROFILE_DIR, cal.CALIB_ROOT = old


@check
def the_profile_is_calibrated_with_every_guess_accepted_or_the_run_goes_without_hud():
    """no profile: guided calibration with auto_accept on the 3 longest recordings at once (launch-fixes: launch
    night tried one); PASS uses it; stars missing tries the next recordings; FAIL or a run that can't start means
    NO-HUD, with the calibration's own reason; a finished profile is used as it is, unless its last guided check
    FAILED"""
    durations = {"short": 100.0, "long": 900.0, "mid": 400.0, "tiny": 50.0}
    vids = [Path(f"{s}.webm") for s in durations]

    def case(results, existing=None, tries=3):
        def inner(tmp):
            if existing:
                existing(tmp)
            run, calls = stand_in_guided(results)
            got = launch.choose_profile("gta6", "gta6", vids, durations, 6, log=lambda m: None, guided_run=run,
                                        tries=tries)
            return got, calls
        return with_profile_dirs(inner)

    ok_run, calls = case(["PASS"])
    stars_later, calls2 = case(["PASS for the minimap. STARS NOT CALIBRATED", "PASS"])
    never_stars, _ = case(["PASS for the minimap. STARS NOT CALIBRATED"] * 3, tries=1)
    failed, _ = case(["FAIL"])
    cannot, _ = case([None])

    def finished(tmp):
        run, _ = stand_in_guided(["PASS"])
        run("gta6", "x.webm")
    def finished_but_failed(tmp):
        run, _ = stand_in_guided(["FAIL"])
        run("gta6", "x.webm")
    kept, calls3 = case([], finished)
    distrusted, _ = case([], finished_but_failed)
    return (ok_run["mode"] == "hud" and ok_run["profile"] == "gta6" and ok_run["stars"]
            and calls[0][1] == ["long.webm", "mid.webm", "short.webm"] and calls[0][2] == 6
            and calls[0][3] is launch.guided.auto_accept
            and stars_later["stars"] and [c[1] for c in calls2] == [["long.webm", "mid.webm", "short.webm"],
                                                                     ["tiny.webm"]]
            and never_stars["mode"] == "hud" and not never_stars["stars"] and len(never_stars["attempts"]) == 3
            and failed["mode"] == "nohud" and failed["profile"] == "gta6_nohud"
            and cannot["mode"] == "nohud" and "video not found: made up" in cannot["why"]
            and kept["mode"] == "hud" and calls3 == []
            and distrusted["mode"] == "nohud")


@check
def auto_accept_takes_every_guess_and_reports_a_profile_it_cannot_save():
    """auto_accept fills every label the scan guessed, confirms the star fit, names the game and saves; when the
    profile can't be saved it returns False and logs why, so the caller falls back"""
    class Session:
        name = "gta6"

        def __init__(self, fail):
            self.fail, self.saved, self.events = fail, False, []
            self.data = {"game": "", "frames": [{"labels": {"hud": None, "stars": None, "phone": None},
                                                 "suggest": {"hud": "shown", "stars": "yes", "phone": None}}],
                         "drawing": {"stars": {"fit": {"box": [1, 2, 3, 4]}}}}

        def event(self, what, **extra):
            self.events.append(what)

        def save(self):
            pass

        def save_profile(self, force):
            if self.fail:
                raise cal.CalibError("not ready to save")
            self.saved = force

    good, bad = Session(False), Session(True)
    return (launch.guided.auto_accept(good) is True and good.saved is True and good.data["game"] == "GTA 6"
            and good.data["frames"][0]["labels"] == {"hud": "shown", "stars": "yes", "phone": None}
            and good.data["drawing"]["stars"]["fit"]["confirmed"] is True
            and launch.guided.auto_accept(bad) is False and "auto_accept_failed" in bad.events)


# --- 4. Is the HUD reading trustworthy? ---

@check
def the_hud_must_agree_with_the_model_or_the_recording_goes_no_hud():
    """a HUD that sees the minimap where the model sees gameplay passes; one that misses it on gameplay sheets, one
    whose stars flicker, and one with the HUD rules off all fail with a plain reason"""
    idx = made_up_idx()
    good = launch.hud_check(idx, made_up_timeline(made_up_counts(), no_minimap=((320, 400),)))
    blind = made_up_timeline(made_up_counts(), no_minimap=((0, 1200),))
    blind["on"] = True
    missed = launch.hud_check(idx, blind)
    flicker = launch.hud_check(idx, made_up_timeline([(i // 4) % 2 for i in range(1200)]))
    off_tl = made_up_timeline(made_up_counts())
    off_tl["on"], off_tl["minimap_share"] = False, 0.05
    off = launch.hud_check(idx, off_tl)
    return (good[0] and good[1] == [] and not missed[0] and "missing" in missed[1][0]
            and not flicker[0] and any("times a minute" in r for r in flicker[1])
            and not off[0] and "5%" in off[1][0])


# --- 5. Moments ---

@check
def a_window_gets_points_for_each_thing_it_shows():
    """the chase window with the two star rises, the crash and police scores action, star rise, stars, activity,
    crash, police and confidence; a calm drive scores far less; NO-HUD mode has no star terms at all"""
    rec = made_up_rec()
    chase, terms = launch.score_window(rec, 105.0, 135.0)
    calm, _ = launch.score_window(rec, 10.0, 40.0)
    _, no_hud = launch.score_window(made_up_rec("nohud"), 105.0, 135.0)
    return (terms["star_rise"] == 4.0 and terms["stars"] == 1.0 and terms["activity"] == 2.0
            and terms["activity_label"] == "chase" and terms["crash"] == 1.5 and terms["police"] == 0.5
            and terms["action"] == 2.0 and chase > calm + 6
            and "star_rise" not in no_hud and "stars" not in no_hud and no_hud["crash"] == 1.5)


@check
def moments_never_overlap_never_use_footage_or_what_earlier_shorts_showed():
    """mine() keeps windows inside clean stretches (never the cutscene at 160-200), apart from each other, and away
    from spans an earlier Short showed; the denied line "no police or crashes" scores no crash"""
    rec = made_up_rec()
    moments = launch.mine(rec)
    blocked = launch.mine(rec, used=[("rec_a", 95.0, 165.0)])
    clean = all(any(a + 0.5 <= m["start_s"] and m["end_s"] <= b - 0.5 for a, b in rec["spans"]) for m in moments)
    apart = all(not (a["start_s"] < b["end_s"] + launch.APART_S and b["start_s"] < a["end_s"] + launch.APART_S)
                for i, a in enumerate(moments) for b in moments[i + 1:])
    beach = [m for m in moments if 210 <= m["start_s"] < 300]
    return (clean and apart and moments[0]["category"] == "star rise" and 95 <= moments[0]["start_s"] <= 130
            and all(m["end_s"] <= 95 or m["start_s"] >= 165 for m in blocked)
            and all("crash" not in m["terms"] for m in beach))


@check
def three_picks_come_from_three_parts_of_the_session():
    """one pick per third of the session; an empty third is filled by the best moment at least 5 min from the others;
    a category already picked costs a point; a moment that fails the safety check is skipped and listed"""
    def m(name, session_s, score, category="chase"):
        return {"video": name, "start_s": session_s, "end_s": session_s + 30, "session_s": session_s,
                "score": score, "category": category}
    ms = [m("a", 100, 9), m("b", 200, 8.5), m("c", 1100, 5), m("d", 1150, 5.5, "crash"), m("e", 2500, 4),
          m("f", 2600, 9, "stunt")]
    picks, _ = launch.pick_three(ms, 3000.0, log=lambda x: None)
    picked = {p["video"] for p in picks}
    unsafe, rejected = launch.pick_three(ms, 3000.0, ok=lambda x: (x["video"] != "a", ["phone on screen"]),
                                         log=lambda x: None)
    gap = [m("a", 100, 9), m("b", 200, 8.5), m("c", 450, 8), m("e", 2500, 4)]
    filled, _ = launch.pick_three(gap, 3000.0, log=lambda x: None)
    return (picked == {"a", "d", "f"} and [p["third"] for p in sorted(picks, key=lambda p: p["session_s"])] == [1, 2, 3]
            and {p["video"] for p in unsafe} == {"b", "d", "f"} and rejected[0]["why"] == ["phone on screen"]
            and {p["video"] for p in filled} == {"a", "c", "e"})


@check
def the_type_follows_what_the_moment_has():
    """the shootout with an explosion becomes an ACTION CLIP (15-30 s round it); the chase with two star rises,
    police and a crash has enough to describe for NARRATED (24-35 s); in NO-HUD mode no stars evidence exists"""
    rec = made_up_rec()
    recs = {"rec_a": rec}
    moments = launch.mine(rec)
    chase = next(m for m in moments if 95 <= m["start_s"] <= 130)
    shoot = next(m for m in moments if 380 <= m["start_s"] <= 400)
    picks = launch.choose_types([dict(chase), dict(shoot)], recs)
    by_start = {p["start_s"]: p for p in picks}
    c, s = by_start[chase["start_s"]], by_start[shoot["start_s"]]
    a0, a1 = s["window"]
    n0, n1 = c["window"]
    kinds = {e["kind"] for e in c["evidence"]}
    nohud = launch.evidence(made_up_rec("nohud"), 100.0, 135.0)
    return (s["type"] == "action" and 15 <= a1 - a0 <= 30 and a0 <= 408 + 3 <= a1
            and c["type"] == "narrated" and 24 <= n1 - n0 <= 35 and n0 >= 0 and n1 <= 160 - 0.5
            and {"activity", "stars", "police", "event", "seen"} <= kinds
            and [e["id"] for e in c["evidence"]] == [f"E{k}" for k in range(1, len(c["evidence"]) + 1)]
            and any(e["kind"] == "stars" and e["numbers"] == [1, 2] for e in c["evidence"])
            and not any(e["kind"] == "stars" for e in nohud))


@check
def all_picks_one_type_switches_the_closest_one():
    """when every pick came out the same type and one of them fits the other type too, that one switches, so the
    night's Shorts have both types"""
    def pick(fit_a, fit_n, fits_n):
        return {"start_s": 0, "end_s": 30, "span": [0, 100], "terms": {},
                "options": {"action": {"window": [0, 20], "evidence": [], "fits": True, "fit": fit_a},
                            "narrated": {"window": [0, 30], "evidence": [], "fits": fits_n, "fit": fit_n}}}
    picks = [pick(5, 1, False), pick(3, 2.5, True), pick(6, 2, True)]
    old = launch.type_options
    launch.type_options = lambda rec, m: dict(m["options"], anchor_s=0.0)
    try:
        out = launch.choose_types(picks, _recs_for(picks))
    finally:
        launch.type_options = old
    return [p["type"] for p in out] == ["action", "narrated", "action"] and "both types" in out[1]["type_why"]


def _recs_for(picks):
    for p in picks:
        p["video"] = "x"
    return {"x": None}


# --- 2. Safety ---

def stand_in_checker(tmp, rules, subs=()):
    """A safety.Checker whose model answers by time: rules is [(from_s, to_s, key)], and a sheet says yes to key when
    it overlaps from..to. A key of None gives no usable answer. subs is [(from_s, to_s, words)]: the subtitles the
    model reads in that stretch (None: no usable reading). The sheets asked about are recorded with their spacing."""
    asked, read = [], []

    def sheets(path, a, b, every, work, subtitles=False):
        return [(f0, f1, f"{f0}|{f1}|{every}|{int(subtitles)}") for f0, f1, _ in safety.sheet_times(a, b, every)]

    def ask(model, prompt, jpg, caps, schema=None, parse=None, options=None):
        f0, f1, every, sub = (float(x) for x in str(jpg).split("|"))
        if sub:
            read.append((f0, f1))
            times = [x for _, _, ts in safety.sheet_times(f0, f1, every) for x in ts]
            strips = []
            for x in times + [None] * (4 - len(times)):
                words = next((w for a, b, w in subs if x is not None and a <= x < b), "")
                if words is None:
                    return {"answer": None, "error": "unusable answer: made up", "wall_s": 0.1}
                strips.append(words)
            return {"answer": parse(json.dumps({f"strip_{k + 1}": s for k, s in enumerate(strips)})), "error": None,
                    "wall_s": 0.1}
        asked.append((f0, f1, every))
        answer = {"note": "a made-up answer", **{k: False for k in safety.WHAT}}
        for a, b, key in rules:
            if f0 < b and a < f1:
                if key is None:
                    return {"answer": None, "error": "unusable answer: made up", "wall_s": 0.1}
                answer[key] = True
        return {"answer": parse(json.dumps(answer)), "error": None, "wall_s": 0.1}

    checker = safety.Checker("GTA 6", tempfile.mkdtemp(dir=tmp), ask=ask, sheets=sheets,
                             info={"digest": "x", "capabilities": []}, log=lambda m: None,
                             ram=lambda: 20.0, unload=lambda model: None)
    checker.read_log = read
    return checker, asked


@check
def the_safety_checks_unload_the_model_when_memory_runs_low():
    """launch-fixes: launch night's first prepare was stopped by Claude Code during the safety checks (Ollama 8.5 GB,
    3.8 GB free), which had no memory guard. Now, as in the index: under 5 GB free the model is unloaded (at most
    every 10 calls) and loads again on the next call; under 2 GB even then, the run stops with a plain reason and
    the answers so far stay cached"""
    with tempfile.TemporaryDirectory() as tmp:
        checker, _ = stand_in_checker(tmp, [])
        frees, unloaded = iter([20.0] * 10 + [4.0, 9.0] + [20.0] * 20), []
        checker.ram, checker.unload = lambda: next(frees), unloaded.append
        checker.look("rec.webm", 0.0, 240.0, 4.0, Path(tmp))
        low, _ = stand_in_checker(tmp, [])
        low.ram = lambda: 1.0
        stopped = None
        try:
            low.look("rec.webm", 0.0, 8.0, 4.0, Path(tmp))
        except safety.MemoryError_ as e:
            stopped = str(e)
    return (unloaded == [launch.index.MODEL] and checker.reloads == 1 and checker.calls >= 12
            and stopped and "memory" in stopped and "cached" in stopped)


def shoot_moment(rec):
    return next(m for m in launch.mine(rec) if 380 <= m["start_s"] <= 400)


@check
def safety_answers_must_be_complete():
    """the safety answer needs a note and true/false for all six questions; anything else is unusable. launch-fixes
    (safety_v3): what the frames show (crash, shooting, police ...) is true/false too; one left out counts as not
    seen, one answered with anything else makes the answer unusable"""
    good = safety.parse_answer(json.dumps({"note": "car on a road", **{k: False for k in safety.WHAT}}))
    seen = safety.parse_answer(json.dumps({"note": "a car hits a wall", **{k: False for k in safety.WHAT},
                                           "crash": True}))
    bad = 0
    for reply in ("not json", "[]", json.dumps({"note": "x", "phone_screen": "yes"}),
                  json.dumps({"note": "", **{k: False for k in safety.WHAT}}),
                  json.dumps({"note": "x", **{k: False for k in safety.WHAT}, "crash": "yes"})):
        try:
            safety.parse_answer(reply)
        except ValueError:
            bad += 1
    return (good["phone_screen"] is False and good["note"] == "car on a road" and bad == 5
            and all(good[k] is False for k in safety.SEEN) and seen["crash"] is True and seen["police"] is False)


@check
def safety_sheets_are_4_frames_a_second_apart():
    """make_sheets() on a real 20 s video: 2..12 s at one frame a second is 3 sheets (4, 4 and 2 frames), each a
    2x2 grid of 640 px frames"""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src.mp4"
        launch.subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=20",
                               "-pix_fmt", "yuv420p", str(src)], capture_output=True)
        sheets = safety.make_sheets(src, 2.0, 12.0, 1.0, Path(tmp) / "work")
        size = launch.subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of",
                                      "csv=p=0", str(sheets[0][2])], capture_output=True, text=True).stdout.strip()
    return [(a, b) for a, b, _ in sheets] == [(2.0, 6.0), (6.0, 10.0), (10.0, 12.0)] and size == "1280,720"


@check
def a_phone_menu_cutscene_or_loading_screen_drops_the_footage_it_is_in():
    """the vision model says phone on screen at 6:45-6:49 (inside the shootout moment's footage): the moment fails
    with the reason; a phone only in footage no type shows changes nothing; a sheet with no usable answer counts as
    unsafe; a menu or a cutscene fails it too. The phone is caught with no phone calibration (the profile has none)"""
    rec = made_up_rec()
    m = shoot_moment(rec)
    with tempfile.TemporaryDirectory() as tmp:
        checker, _ = stand_in_checker(tmp, [(405.0, 409.0, "phone_screen")])
        bad = safety.check_moment(dict(m), rec, tmp, checker)
        checker, _ = stand_in_checker(tmp, [(590.0, 600.0, "phone_screen")])
        fine = safety.check_moment(dict(m), rec, tmp, checker)
        checker, _ = stand_in_checker(tmp, [(410.0, 412.0, None)])
        unanswered = safety.check_moment(dict(m), rec, tmp, checker)
        checker, _ = stand_in_checker(tmp, [(395.0, 399.0, "menu_or_map"), (405.0, 409.0, "cutscene")])
        menu = safety.check_moment(dict(m), rec, tmp, checker)
    return (bad[0] is False and "phone on screen" in " ".join(bad[1]) and fine[0] is True
            and unanswered[0] is False and "no usable answer" in " ".join(unanswered[1])
            and menu[0] is False and "cutscene" in " ".join(menu[1]))


@check
def blood_in_the_first_7_s_moves_the_start_once_or_drops_the_type():
    """blood at the very start of the ACTION window moves its start 7 s later and its end with it where the clean
    footage allows (still round its moment, still 15 s or more); blood all through it drops the type; blood later in the Short is only a flag, and so are subtitles;
    the first 7 s are looked at twice a second"""
    rec = made_up_rec()
    m = shoot_moment(rec)
    a0, a1 = launch.type_options(rec, m)["action"]["window"]
    with tempfile.TemporaryDirectory() as tmp:
        checker, asked = stand_in_checker(tmp, [(a0, a0 + 2.0, "blood_or_gore")])
        moved_m = dict(m)
        moved = safety.check_moment(moved_m, rec, tmp, checker)
        checker, _ = stand_in_checker(tmp, [(a0, a1, "blood_or_gore")])
        gone_m = dict(m)
        safety.check_moment(gone_m, rec, tmp, checker)
        checker, _ = stand_in_checker(tmp, [(a1 - 3.0, a1, "blood_or_gore"), (a1 - 3.0, a1, "subtitles")])
        later_m = dict(m)
        later = safety.check_moment(later_m, rec, tmp, checker)
    return (moved[0] and moved_m["safe_options"].get("action") == [round(a0 + 7.0, 2), round(max(a1, a0 + 22.0), 2)]
            and moved_m["safety"]["types"]["action"]["moved_for_gore"]
            and any(every == 0.5 for _, _, every in asked)
            and "action" not in gone_m["safe_options"]
            and later[0] and later_m["safe_options"]["action"] == [a0, a1]
            and any(f.startswith("BLOOD") for f in later_m["safety"]["flags"])
            and any(f.startswith("SUBTITLES") for f in later_m["safety"]["flags"]))


@check
def swear_words_are_found_and_mild_words_are_not():
    """PROFANITY finds strong and moderate swearing, stretched and censored spellings and slurs, and leaves mild words
    (damn, hell) and innocent words that hold one (class, assist, cockpit, Dickens) alone; the model's reading of a
    subtitle sheet must be 4 strips of text"""
    found = [safety.profanity(s) for s in ("Hey, they on us! Fuck! Fuck!", "Watch your ass.", "Shhiitttt!!",
                                            "What the f**k", "motherfucker", "Bullshit.")]
    clean = [safety.profanity(s) for s in ("What the hell!? damn", "pass the class, assist", "cockpit", "Dickens",
                                            "Take out the Cops before getting to the rendezvous point.", "")]
    good = safety.parse_subtitles(json.dumps({"strip_1": "Hey  there", "strip_2": "", "strip_3": "x", "strip_4": ""}))
    bad = 0
    for reply in ("nope", json.dumps({"strip_1": "a"}), json.dumps({f"strip_{k}": 1 for k in range(1, 5)})):
        try:
            safety.parse_subtitles(reply)
        except ValueError:
            bad += 1
    return (all(found) and found[0] == ["fuck", "fuck"] and found[3] == ["f**k"] and not any(clean)
            and good == ["Hey there", "", "x", ""] and bad == 3)


@check
def swearing_in_the_first_7_s_moves_the_start_or_drops_the_type():
    """subtitles that swear in the first 7 s of the ACTION window move its start 7 s later, like blood; swearing all
    through its first 14 s drops it; swearing later in the Short is only a PROFANITY flag; unreadable subtitles in the
    first 7 s count as swearing (unchecked words are not safe)"""
    rec = made_up_rec()
    m = shoot_moment(rec)
    a0, a1 = launch.type_options(rec, m)["action"]["window"]
    with tempfile.TemporaryDirectory() as tmp:
        checker, _ = stand_in_checker(tmp, [], subs=[(a0, a0 + 2.0, "Hey! Fuck!")])
        moved_m = dict(m)
        moved = safety.check_moment(moved_m, rec, tmp, checker)
        checker, _ = stand_in_checker(tmp, [], subs=[(a0, a0 + 16.0, "Shit, shit, shit")])
        gone_m = dict(m)
        safety.check_moment(gone_m, rec, tmp, checker)
        checker, _ = stand_in_checker(tmp, [(a1 - 4.0, a1, "subtitles")], subs=[(a1 - 3.0, a1, "Watch your ass.")])
        later_m = dict(m)
        later = safety.check_moment(later_m, rec, tmp, checker)
        checker, _ = stand_in_checker(tmp, [], subs=[(a0, a0 + 2.0, None)])
        blind_m = dict(m)
        safety.check_moment(blind_m, rec, tmp, checker)
    return (moved[0] and moved_m["safe_options"]["action"][0] == round(a0 + 7.0, 2)
            and "action" not in gone_m["safe_options"]
            and later[0] and later_m["safe_options"]["action"] == [a0, a1]
            and any(f.startswith("PROFANITY") and "ass" in f for f in later_m["safety"]["flags"])
            and blind_m["safe_options"].get("action", [a0])[0] != a0)


@check
def subtitle_sheets_cut_the_band_at_full_size():
    """make_sheets(subtitles=True) on a real 1920x1080 video: each frame is cut to its subtitle band and 4 are
    stacked, 1280 px wide, one a second"""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src.mp4"
        launch.subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=10",
                               "-pix_fmt", "yuv420p", str(src)], capture_output=True)
        sheets = safety.make_sheets(src, 1.0, 9.0, 1.0, Path(tmp) / "work", subtitles=True)
        w, h = (int(v) for v in launch.subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(sheets[0][2])],
            capture_output=True, text=True).stdout.strip().split(","))
    band_h = round(1280 * (1080 * safety.SUBTITLE_BAND[3]) / (1920 * safety.SUBTITLE_BAND[2]))
    return [(a, b) for a, b, _ in sheets] == [(1.0, 5.0), (5.0, 9.0)] and w == 1280 and abs(h - 4 * band_h) <= 8


@check
def the_index_saying_blood_counts_as_gore_too():
    """a line of the index ("a body on the ground with blood") in the first 7 s counts as gore even when the safety
    question says no; "no blood" doesn't"""
    rec = made_up_rec()
    a0 = launch.type_options(rec, shoot_moment(rec))["action"]["window"][0]
    sheet = max((s for s in rec["sheets"] if s["t"] <= a0), key=lambda s: s["t"])
    sheet["notable"] = "Player stands over a body on the ground with blood"
    hits = safety.index_gore(rec, a0, a0 + 7.0)
    sheet["notable"] = "Player shoots at gang members, no blood"
    none = safety.index_gore(rec, a0, a0 + 7.0)
    return len(hits) == 1 and "body" in hits[0][2] and none == []


@check
def no_hud_mode_asks_twice_as_often():
    """with the HUD rules off the safety question is the only guard, so it looks every half second, not every second"""
    with tempfile.TemporaryDirectory() as tmp:
        rec = made_up_rec("nohud")
        checker, asked = stand_in_checker(tmp, [])
        safety.check_moment(dict(shoot_moment(rec)), rec, tmp, checker)
        rec2 = made_up_rec()
        checker2, asked2 = stand_in_checker(tmp, [])
        safety.check_moment(dict(shoot_moment(rec2)), rec2, tmp, checker2)
    return {e for _, _, e in asked} == {0.5} and {e for _, _, e in asked2} == {1.0, 0.5}


@check
def the_type_keeps_to_what_the_safety_checks_left():
    """when the safety checks dropped NARRATED from the chase moment, it becomes an ACTION CLIP; a start the checks
    moved for gore is the start that is used, with its own evidence"""
    rec = made_up_rec()
    chase = next(m for m in launch.mine(rec) if 95 <= m["start_s"] <= 130)
    opts = launch.type_options(rec, chase)
    a0, a1 = opts["action"]["window"]
    n0, n1 = opts["narrated"]["window"]
    one = dict(chase, safe_options={"action": [a0, a1]})
    moved = dict(chase, safe_options={"action": [a0, a1], "narrated": [n0 + 7.0, n1]})
    out = launch.choose_types([one, moved], {"rec_a": rec})
    return (out[0]["type"] == "action" and out[0]["window"] == [a0, a1] and out[1]["type"] == "narrated"
            and out[1]["window"] == [n0 + 7.0, n1] and all(e["to_s"] > n0 + 7.0 for e in out[1]["evidence"]))


@check
def footage_an_earlier_short_showed_never_comes_back():
    """every span in _used_footage.json counts, not only the last 5 Shorts' (a pinned one too): mine() leaves them
    all out"""
    with tempfile.TemporaryDirectory() as tmp:
        for k in range(8):
            footage_short.save_used(tmp, f"short{k}", [["rec_a", 100.0 + k, 101.0 + k]])
        footage_short.save_used(tmp, "pinned", [["rec_a", 400.0, 430.0]])
        used = launch.all_used_spans(tmp)
    moments = launch.mine(made_up_rec(), used)
    return (len(used) == 9 and all(m["end_s"] <= 100.0 or m["start_s"] >= 108.0 for m in moments)
            and all(m["end_s"] <= 400.0 or m["start_s"] >= 430.0 for m in moments))


@check
def a_render_over_minus_1_dbtp_is_limited_and_measured_again():
    """a made-up mp4 whose audio peaks over 0 dBTP is limited until ffmpeg's true-peak meter reads under -1.0 dBTP
    (the picture copied as it is); a quiet one is left alone; one a limiter can't fix stops with SafetyError"""
    with tempfile.TemporaryDirectory() as tmp:
        loud, quiet = Path(tmp) / "loud.mp4", Path(tmp) / "quiet.mp4"
        for out, vol in ((loud, "volume=24dB"), (quiet, "volume=-6dB")):
            launch.subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=3",
                                   "-f", "lavfi", "-i", "sine=f=997:d=3", "-af", f"{vol},aformat=channel_layouts=stereo",
                                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                                   "-shortest", str(out)], capture_output=True)
        before = safety.true_peak(loud)
        fixed = safety.guard_true_peak(loud, log=lambda m: None)
        after = safety.true_peak(loud)
        left = safety.guard_true_peak(quiet, log=lambda m: None)
        try:
            safety.guard_true_peak(quiet, log=lambda m: None, measure=lambda p: 0.5,
                                   limit=lambda s, d, target: shutil.copyfile(s, d))
            stuck = False
        except safety.SafetyError:
            stuck = True
        video = launch.subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                                       "stream=codec_name", "-of", "csv=p=0", str(loud)], capture_output=True,
                                      text=True).stdout.strip()
    return (before > 0.0 and after < -1.0 and fixed["after"] == after and fixed["limited_to"] is not None
            and left["passes"] == 0 and left["before"] < -1.0 and stuck and video == "h264")


# --- 3. Truth rules and the render stage ---

FACTS = {
    "hollow": {"id": "hollow", "claim": "Wanted stars turn hollow when the police lose sight of you.",
               "safe_wording": "stars reportedly turn hollow when the cops lose sight of you", "status": "reported",
               "time_sensitive": False, "checked_on": "2026-11-17", "source": "https://example.com/a"},
    "release": {"id": "release", "claim": "GTA 6 launches on November 19, 2026.", "safe_wording":
                "launches on November 19, 2026", "status": "confirmed", "time_sensitive": True,
                "checked_on": "2026-11-17", "source": "https://example.com/b"},
    "leak": {"id": "leak", "claim": "A leak.", "safe_wording": "a leak", "status": "unverified",
             "time_sensitive": False, "checked_on": None},
}
TODAY = "2026-11-19"


def chase_pick(mode="hud"):
    """The chase moment of the made-up recording as prepare would pick it: NARRATED, with its evidence."""
    rec = made_up_rec(mode)
    m = next(m for m in launch.mine(rec) if 95 <= m["start_s"] <= 130)
    pick = launch.choose_types([dict(m)], {"rec_a": rec})[0]
    pick.update(id="M1", short=1, path=rec["path"], mode=mode)
    return pick


def ev(pick, kind, value=None):
    return next(e["id"] for e in pick["evidence"] if e["kind"] == kind and (value is None or value in str(e["value"])))


def good_narrated(pick):
    """A script that keeps every rule with the stand-in voice's timing (5.8 s a line from 1:43.5): each line cites
    what is on screen while it is spoken. Since PR 26 it also keeps the story rules: a format, what each line
    teaches, no play-by-play, one payoff and a last line that loops into the hook."""
    police, first, two, crash = ev(pick, "police"), ev(pick, "stars", "0 to 1"), ev(pick, "stars", "1 to 2"),         ev(pick, "event", "crash")
    late = next(e["id"] for e in pick["evidence"] if e["kind"] == "police" and e["from_s"] >= 130)
    hook = "The cops are right behind me"
    return {"short": 1, "moment": "M1", "type": "narrated", "hooks": [hook, "My first chase got loud", "Two stars came fast"],
            "star": 0, "title": "My FIRST real chase went wrong fast", "description":
            "The wanted level climbs while a police car follows me. Then it hits a wall behind me.",
            "hashtags": ["#GTA6", "#GTAVI", "#Shorts"], "format": "F3",
            "format_why": "the chase shows the police, two star rises and a crash",
            "lines": [{"say": hook, "claim": "on_screen", "evidence": [police], "facts": [],
                       "teaches": "the police are on me from the start"},
                      {"say": "Then the first wanted star shows up.", "claim": "on_screen", "evidence": [first],
                       "facts": [], "teaches": "when the first wanted star arrives"},
                      {"say": "A few seconds later I have 2 stars.", "claim": "on_screen", "evidence": [two],
                       "facts": [], "teaches": "how fast the second star follows"},
                      {"say": "In GTA 6 the stars reportedly turn hollow when the cops lose sight of you.",
                       "claim": "fact", "evidence": [], "facts": ["hollow"], "teaches": "what hollow stars mean"},
                      {"say": "Then a police car crashes into a wall.", "claim": "on_screen", "evidence": [crash],
                       "facts": [], "payoff": True, "teaches": "the chase ends with a police car crash"},
                      {"say": "And it all starts when", "claim": "on_screen", "evidence": [late], "facts": [],
                       "teaches": "the loop back to the police behind me", "loop": True}]}


@check
def a_narrated_line_must_point_at_what_is_on_screen():
    """the good script passes; a line with no claim type, an on_screen line with no evidence, an unknown evidence
    id, and evidence that is not on screen while the line is spoken are each refused with a plain reason"""
    pick = chase_pick()
    good = good_narrated(pick)
    ok, _, _ = truth.check_short(good, pick, FACTS, TODAY)
    bad = json.loads(json.dumps(good))
    bad["lines"][1].pop("claim")
    bad["lines"][2]["evidence"] = []
    bad["lines"][4]["evidence"] = ["E999"]
    problems, _, _ = truth.check_short(bad, pick, FACTS, TODAY)
    on_time = [(103.5 + 6 * k, 109.5 + 6 * k) for k in range(5)]
    early = on_time[:4] + [(110.0, 116.0)]                      # the crash (2:12) is not on screen during line 5
    timed, _, _ = truth.check_short(good, pick, FACTS, TODAY, on_time)
    late, _, _ = truth.check_short(good, pick, FACTS, TODAY, early)
    text = " ".join(problems)
    return (ok == [] and timed == [] and "line 2: needs \"claim\"" in text
            and "line 3: an on_screen line must cite" in text and "no evidence E999" in text
            and any("line 5: none of its evidence" in p for p in late) and not any("line 2: none" in p for p in late))


@check
def words_and_numbers_need_their_evidence():
    """"stars" needs a HUD star item, "crash" a crash, "police" police (or a chase, or stars); a number must be in
    the evidence ("1 to 2 stars" is, "5 stars" is not) or in a listed fact; in NO-HUD mode no line may mention stars
    at all"""
    pick = chase_pick()
    by = {e["id"]: e for e in pick["evidence"]}
    seen_only = next(e["id"] for e in pick["evidence"] if e["kind"] == "seen")

    def line(say, evidence):
        return truth.line_problems(1, {"say": say, "claim": "on_screen", "evidence": evidence, "facts": []},
                                   by, False, FACTS)
    rise = ev(pick, "stars", "1 to 2")
    good = line("My wanted level goes from 1 star to 2 stars.", [rise])
    crash_no = line("Then I crash into a wall.", [rise])
    five = line("I hit 5 stars here.", [rise])
    cops_ok = line("The police are on me now.", [ev(pick, "activity", "chase")])
    nohud = truth.line_problems(1, {"say": "Two stars already.", "claim": "on_screen", "evidence": [seen_only],
                                    "facts": []}, by, True, FACTS)
    return (good == [] and any("\"crash\"" in p for p in crash_no) and any("number 5" in p for p in five)
            and cops_ok == [] and any("NO-HUD" in p for p in nohud))


@check
def claims_about_how_the_game_works_need_a_fact():
    """"you can", "the game", "cops will", "lets you" are refused without a fact and pass with one; a reported fact
    must keep its safe wording, an unverified one is refused, a stale time-sensitive one is refused"""
    pick = chase_pick()
    by = {e["id"]: e for e in pick["evidence"]}
    rise = ev(pick, "stars", "1 to 2")
    refused = [truth.line_problems(1, {"say": s, "claim": "on_screen", "evidence": [rise], "facts": []}, by, False,
                                   FACTS)
               for s in ("You can lose two stars by hiding.", "The game gives you a star for this.",
                         "Cops will always follow you now.", "This lets you escape faster.")]
    lines = [{"say": "Stars turn hollow when cops lose you.", "claim": "fact", "facts": ["hollow"], "evidence": []},
             {"say": "This is a leak.", "claim": "fact", "facts": ["leak"], "evidence": []},
             {"say": "It launches on November 19, 2026.", "claim": "fact", "facts": ["release"], "evidence": []}]
    fresh, _ = truth.fact_problems(lines, FACTS, "2026-11-19")
    stale, _ = truth.fact_problems(lines[2:], FACTS, "2026-11-25")
    return (all(any("how the game works" in p for p in r) for r in refused)
            and any("safe wording" in p for p in fresh) and any("unverified" in p for p in fresh)
            and not any("line 3" in p for p in fresh) and any("last checked" in p for p in stale))


@check
def only_jason_and_lucia_may_be_named_and_no_spoilers():
    """Jason, Lucia, GTA 6, GTA VI and PS5 pass anywhere (also in a hook or title); another capitalised name mid
    sentence, a trailer name even in capitals, and a story or mission word are refused"""
    def p(text):
        return truth.name_problems(text, "", "x") + truth.spoiler_problems(text, "x")
    return (p("Jason and Lucia drive through GTA 6 on PS5.") == [] and p("My first hour of GTA VI with Lucia.") == []
            and p("Then Michael shows up.") and p("BOOBIE is here.") and p("This is the final mission.")
            and p("The story ends here.") and p("Watch what happens next.") == [])


@check
def titles_and_hooks_may_use_title_case():
    """launch-fixes: a title or a hook in normal Title Case passes ("How To Make GTA 6 Cops FORGET You"); a trailer
    name is still refused there, and a capitalised name mid sentence is still refused in a line or a description"""
    pick = chase_pick()
    items = pick["evidence"]
    title = truth.text_problems("How To Make GTA 6 Cops FORGET You", items, False, "title")
    hook = truth.text_problems("Can The Cops Catch Me?", items, False, "hook 1")
    named = truth.text_problems("Driving With Boobie Tonight", items, False, "title")
    desc = truth.text_problems("A chase starts. Then Michael shows up.", items, False, "description")
    return title == [] and hook == [] and any("boobie" in p for p in named) and any("Michael" in p for p in desc)


@check
def hooks_titles_and_descriptions_follow_the_rules():
    """3 hooks of 3-6 words, no emoji, a starred index; the title by make_short's rules; exactly 2 sentences of
    description; narrated line 1 is the starred hook word for word; an ACTION CLIP has no lines"""
    pick = chase_pick()
    good = good_narrated(pick)
    bad = dict(good, hooks=["Too short", good["hooks"][1], "This one has way too many words in it now"], star=0,
               description="One sentence only.", lines=[dict(good["lines"][0], say="Something else."), *good["lines"][1:]])
    problems, _, _ = truth.check_short(bad, pick, FACTS, TODAY)
    problems += truth.check_short(dict(good, star=5), pick, FACTS, TODAY)[0]
    action = dict(pick, type="action")
    with_lines, _, _ = truth.check_short(dict(good, type="action"), action, FACTS, TODAY)
    text = " ".join(problems)
    return ("hook 1: 2 words" in text and "hook 3: 10 words" in text and "star:" in text
            and "1 sentence(s)" in text and "line 1 must be the starred hook" in text
            and any("ACTION CLIP has no narration" in p for p in with_lines))


@check
def a_short_may_start_later_inside_its_window():
    """start_s moves a Short's start later (to skip a swear word in the subtitles) and drops the evidence that ends
    before it; a start that leaves less than the type's minimum, or isn't a number, is refused"""
    pick = chase_pick()
    w0, w1 = pick["window"]
    later, ok = launch.trimmed(pick, w0 + 6.0)
    _, too_late = launch.trimmed(pick, w1 - 10.0)
    _, word = launch.trimmed(pick, "soon")
    return (ok == [] and later["window"] == [w0 + 6.0, w1] and all(e["to_s"] > w0 + 6.0 for e in later["evidence"])
            and len(later["evidence"]) < len(pick["evidence"]) and pick["window"] == [w0, w1]
            and "start_s" in too_late[0] and "start_s" in word[0])


@check
def an_auto_peak_must_come_from_evidence():
    """an ACTION CLIP's peak must name one of the moment's peak options (a star rise or a crash, explosion or stunt
    sheet) and lie within 1 s of it inside the clip; no peak, or --no-cold-open, means no cold open"""
    m = {"window": [400.0, 425.0], "peak_options": [{"evidence": "E5", "from_s": 408.0, "to_s": 414.0}]}
    good = launch.check_peak({"peak": {"evidence": "E5", "at_s": 411.2}}, m, False)
    far = launch.check_peak({"peak": {"evidence": "E5", "at_s": 420.0}}, m, False)
    other = launch.check_peak({"peak": {"evidence": "E2", "at_s": 411.0}}, m, False)
    none = launch.check_peak({}, m, False)
    off = launch.check_peak({"peak": {"evidence": "E5", "at_s": 411.2}}, m, True)
    return (good == (411.2, []) and far[0] is None and "within" in far[1][0] and other[0] is None
            and "not one of" in other[1][0] and none == (None, []) and off == (None, []))


def run_folder_with(tmp, picks, shorts, dry=False):
    folder = Path(tmp) / "launch-2026-11-19"
    folder.mkdir(parents=True)
    cand = {"game": "gta6", "game_name": "GTA 6", "dry_run": dry, "picks": picks, "settings": {"library": str(tmp)},
            "recordings": [{"name": "rec_a.webm", "path": picks[0]["path"], "duration_s": 600.0, "mode": "hud",
                            "hud_check": None}],
            "calibration": {"profile": "gta6", "why": "made up"}, "timing_s": {"index": 75.0}}
    (folder / "candidates.json").write_text(json.dumps(cand), encoding="utf-8")
    (folder / "shorts.json").write_text(json.dumps({"shorts": shorts}), encoding="utf-8")
    return folder


def stand_in_voice(path, root):
    """Every line 5.8 s, shared by its beats; a 1.5 s loop ending."""
    script = json.loads(Path(path).read_text(encoding="utf-8"))
    lines = [[5.8 / len(l["beats"])] * len(l["beats"]) for l in script["lines"]]
    return {"total_s": 5.8 * len(lines), "lines": lines, "loop_s": 1.5, "first": (0, 0), "behind": {}}


def stand_in_explainer(resolved, out):
    script = json.loads(Path(resolved).read_text(encoding="utf-8"))
    visuals = [{"line": n + 1, "source": f"{b['file']}@{b['at']}", "seconds": 5.8 / len(l["beats"]),
                "source_start_s": float(b["at"]), "loop_tail": False}
               for n, l in enumerate(script["lines"]) for b in l["beats"]]
    (Path(out) / "log.json").write_text(json.dumps({"visuals": visuals, "durations": {"video_s": 24.0}}), encoding="utf-8")
    made = launch.subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=108x192:d=1",
                                  "-f", "lavfi", "-i", "sine=f=440:d=1", "-c:v", "libx264", "-c:a", "aac", "-shortest",
                                  str(Path(out) / "short.mp4")], capture_output=True)
    return made.returncode == 0


@check
def a_narrated_short_plays_its_moment_on_and_is_logged():
    """render() with a stand-in voice and explainer: the footage plays on from beat to beat (each starts where the
    one before ended) with its game sound, the timed check passes, claims.txt lists every line with its evidence,
    upload.txt has the title, description and hashtags, render.json says READY, the shown footage is logged in
    _used_footage.json, and review.html shows the video, the starred hook, the evidence and the commands"""
    pick = chase_pick()
    with tempfile.TemporaryDirectory() as tmp:
        shorts = Path(tmp) / "shorts"
        shorts.mkdir()
        folder = run_folder_with(tmp, [pick], [good_narrated(pick)])
        data = launch.render(folder, shorts_dir=shorts, today=TODAY, log=lambda m: None, facts=FACTS, checker=None,
                             timing=stand_in_voice, narrated_render=stand_in_explainer, indexes=[made_up_idx()],
                             engine="v1")
        entry = data["shorts"][0]
        out = Path(entry["folder"])
        script = json.loads((out / "script_resolved.json").read_text(encoding="utf-8"))
        claims = (out / "claims.txt").read_text(encoding="utf-8")
        upload = (out / "upload.txt").read_text(encoding="utf-8")
        used = json.loads((shorts / footage_short.USED_FOOTAGE).read_text(encoding="utf-8"))
        page = (folder / "review.html").read_text(encoding="utf-8")
    ats = [float(b["at"]) for l in script["lines"] for b in l["beats"]]
    steps = [b - a for a, b in zip(ats, ats[1:])]
    return (entry["status"] == "READY" and ats[0] == pick["window"][0] and all(s > 0 for s in steps)
            and all(b.get("sound") is True for l in script["lines"] for b in l["beats"])
            and script["voice"] == "am_puck" and "Line 5 (on_screen): Then a police car crashes into a wall." in claims
            and "REPORTED" in claims and "My FIRST real chase went wrong fast" in upload and "#GTAVI" in upload
            and used["shorts"][0]["spans"][0][0] == "rec_a" and "<video" in page and "&#11088; The cops" in page
            and "--hook 2" in page and any(f.startswith("REPORTED FACT") for f in entry["flags"]))


@check
def a_short_that_breaks_a_rule_is_not_rendered_and_says_why():
    """a narrated Short whose line names a crash it can't show is FAILED with the reason on the review page and in
    render.json, nothing is logged as used, and the other Short still renders; a dry run logs nothing either"""
    pick = chase_pick()
    second = dict(pick, short=2, id="M2")
    bad = good_narrated(pick)
    bad["lines"][1]["say"] = "A police car crashes right behind me."
    good = dict(good_narrated(second), short=2, moment="M2")
    with tempfile.TemporaryDirectory() as tmp:
        shorts = Path(tmp) / "shorts"
        shorts.mkdir()
        folder = run_folder_with(tmp, [pick, second], [bad, good])
        data = launch.render(folder, shorts_dir=shorts, today=TODAY, log=lambda m: None, facts=FACTS, checker=None,
                             timing=stand_in_voice, narrated_render=stand_in_explainer, indexes=[made_up_idx()],
                             engine="v1")
        page = (folder / "review.html").read_text(encoding="utf-8")
        used = json.loads((shorts / footage_short.USED_FOOTAGE).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        shorts = Path(tmp) / "shorts"
        shorts.mkdir()
        folder = run_folder_with(tmp, [pick], [good_narrated(pick)], dry=True)
        launch.render(folder, shorts_dir=shorts, today=TODAY, log=lambda m: None, facts=FACTS, checker=None,
                      timing=stand_in_voice, narrated_render=stand_in_explainer, indexes=[made_up_idx()],
                             engine="v1")
        dry_logged = (shorts / footage_short.USED_FOOTAGE).exists()
        dry_page = (folder / "review.html").read_text(encoding="utf-8")
    first, second_e = data["shorts"]
    return (first["status"] == "FAILED" and any("\"crash" in p for p in first["problems"])
            and second_e["status"] == "READY" and len(used["shorts"]) == 1 and "NOT RENDERED" in page
            and "Problems (not rendered)" in page and not dry_logged and "DRY RUN" in dry_page)


def made_up_footage(path, seconds=60):
    """A real video with sound: a moving test picture and a tone with loud bursts."""
    return launch.subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=1280x720:r=30:d={seconds}", "-f", "lavfi",
         "-i", f"sine=f=330:d={seconds}", "-af", "volume='if(lt(mod(t,10),1),8,1)':eval=frame",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", str(path)],
        capture_output=True).returncode == 0


@check
def an_action_clip_gets_its_hook_cold_open_music_and_a_safe_peak():
    """render() of an ACTION CLIP on a real made-up video: the clip is cut to 1080x1920, the starred hook is burned
    in, the auto peak gives a cold open (the Short is longer than its window by the cold open) and is flagged AUTO
    PEAK with the command to drop it; the music bed is mixed under the game sound; the true peak is under -1.0 dBTP;
    --short 1 --no-cold-open renders it again without the cold open, and --hook 1 swaps in the first hook.
    launch-fixes: this is the old way (no voice), kept only behind --engine v2; /launch's default is v3"""
    with tempfile.TemporaryDirectory() as tmp:
        src, music = Path(tmp) / "rec_b.mp4", Path(tmp) / "bed.wav"
        if not made_up_footage(src):
            return False
        launch.subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=40", str(music)],
                              capture_output=True)
        pick = {"id": "M1", "short": 1, "type": "action", "video": "rec_b", "path": str(src), "mode": "hud",
                "window": [20.0, 38.0], "flags": [], "type_why": "made up", "span": [0.0, 60.0],
                "evidence": [{"id": "E1", "kind": "event", "value": "crash", "from_s": 28.0, "to_s": 34.0,
                              "numbers": [], "source": "made up \"car crashes\""}],
                "peak_options": [{"evidence": "E1", "from_s": 28.0, "to_s": 34.0}]}
        short = {"short": 1, "type": "action", "hooks": ["He did not see that", "Wait for the crash", "That wall came fast"],
                 "star": 1, "peak": {"evidence": "E1", "at_s": 30.0}, "title": "This CRASH came out of absolutely nowhere",
                 "description": "A drive ends against a wall. It all happens in one second.",
                 "hashtags": ["#GTA6", "#GTAVI", "#Shorts"]}
        shorts = Path(tmp) / "shorts"
        shorts.mkdir()
        folder = run_folder_with(tmp, [pick], [short])
        old = launch.pick_music
        launch.pick_music = lambda seconds, seed: (music, "made up")
        try:
            first = launch.render(folder, shorts_dir=shorts, today=TODAY, log=lambda m: None, facts=FACTS,
                                  checker=None, indexes=[], engine="v2")["shorts"][0]
            tp = safety.true_peak(first["files"]["short.mp4"]) if first["status"] == "READY" else None
            size = launch.subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                                          "stream=width,height", "-of", "csv=p=0", first["files"]["short.mp4"]],
                                         capture_output=True, text=True).stdout.strip()
            again = launch.render(folder, 1, None, True, shorts_dir=shorts, today=TODAY, log=lambda m: None,
                                  facts=FACTS, checker=None, indexes=[], engine="v2")["shorts"][0]
            swapped = launch.render(folder, 1, 1, False, shorts_dir=shorts, today=TODAY, log=lambda m: None,
                                    facts=FACTS, checker=None, indexes=[], engine="v2")["shorts"][0]
        finally:
            launch.pick_music = old
    return (first["status"] == "READY" and first["hook"] == "Wait for the crash" and first["cold_open"]
            and first["length_s"] > 18.0 + 0.5 and tp is not None and tp < -1.0 and size == "1080,1920"
            and any(f.startswith("AUTO PEAK") for f in first["flags"]) and first["music"] == "bed.wav"
            and any(f.startswith("CRASH") for f in first["flags"])
            and again["status"] == "READY" and again["cold_open"] is None and abs(again["length_s"] - 18.0) < 0.2
            and swapped["hook"] == "He did not see that" and swapped["cold_open"])


# --- 5. The NO-HUD fallback ---

@check
def the_no_hud_profiles_never_see_a_hud():
    """gta5_nohud and gta6_nohud load, their bar rules fail for every colour (so the minimap is never seen and
    index.py's HUD rules are off: no stars, no phone, no no-minimap stretches), and their stars and phone are off"""
    steps = range(0, 256, 51)
    colours = [(r, g, b) for r in steps for g in steps for b in steps]
    out = True
    for name in ("gta5_nohud", "gta6_nohud"):
        prof = clip.load_profile(name)
        never = not any(clip.bar_colour_ok(c, rule) for rule in prof.bar_rules for c in colours)
        tl = index.hud_timeline([(i * 0.5, 0.0, False, False, 0) for i in range(40)], 20.0, prof)
        out &= (never and not prof.stars_on and not prof.phone_on and not tl["on"] and not any(tl["stars"])
                and not any(tl["no_minimap"]) and not any(tl["phone"]))
    return out and clip.load_profile("gta6_nohud").game == "GTA 6"


@check
def a_failed_calibration_runs_the_whole_night_without_hud():
    """when the guided calibration FAILs, every recording is indexed with gta6_nohud, the run says NO-HUD MODE and
    why, no pick has a wanted-star item or a star-rise peak, and every pick is flagged; launch-fixes: every HUD step
    is in the run's logs/hud.txt, and review.html says HUD NOT FOUND, with why, at the very top"""
    def run(tmp):
        tmp = Path(tmp)
        inp, shorts, lib = tmp / "input", tmp / "shorts", tmp / "library"
        for d in (inp, shorts, lib):
            d.mkdir(exist_ok=True)
        (lib / "library.json").write_text(json.dumps({"videos": {}}), encoding="utf-8")
        for name in ("GTA_20261119000000.webm", "GTA_20261119010000.webm"):
            (inp / name).write_bytes(b"x")
        guided_run, _ = stand_in_guided(["FAIL"])
        asked = []

        def index_video(video, profile, every, library, cache_dir, log):
            asked.append(profile)
            idx = made_up_idx(Path(video).stem, video)
            idx.pop("_timeline")
            idx["profile"]["name"] = profile
            return idx

        old = launch.probe_duration
        launch.probe_duration = lambda v: DURATION
        try:
            folder = launch.prepare("gta6", input_dir=inp, shorts_dir=shorts, library_dir=lib, day="2026-11-19",
                                    log=lambda m: None, index_video=index_video, guided_run=guided_run,
                                    clip_runner=lambda v, p: None, check=False, sheets=False, safety=None,
                                    posted_file=Path(tmp) / "posted_topics.json")   # none posted: the owner's real list changes
        finally:
            launch.probe_duration = old
        data = json.loads((folder / "candidates.json").read_text(encoding="utf-8"))
        log = (folder / "logs" / "hud.txt").read_text(encoding="utf-8")
        page = launch.write_review(folder, data, {"shorts": []}, {"shorts": []}).read_text(encoding="utf-8")
        return data, asked, log, page
    old_env = launch.os.environ.get("GTA_CLIPPER_PROFILES")
    try:
        data, asked, log, page = with_profile_dirs(run)
    finally:
        if old_env is None:
            launch.os.environ.pop("GTA_CLIPPER_PROFILES", None)
    picks = data["picks"]
    top = page.find("HUD NOT FOUND")
    return (data["hud"]["mode"] == "nohud" and "FAIL" in data["hud"]["why"] and data["hud"]["log"].endswith("hud.txt")
            and "Guided calibration of gta6" in log and "NO-HUD MODE" in log
            and 0 < top < page.find("What to do") and "FAIL" in page[top:page.find("What to do")]
            and asked == ["gta6_nohud", "gta6_nohud"] and data["calibration"]["mode"] == "nohud"
            and "FAIL" in data["calibration"]["why"] and all(r["mode"] == "nohud" for r in data["recordings"])
            and picks and all(p["mode"] == "nohud" for p in picks)
            and not any(e["kind"] == "stars" for p in picks for e in p["evidence"])
            and not any(o["kind"] == "star rise" for p in picks for o in p.get("peak_options", []))
            and all(any(f.startswith("NO-HUD MODE") for f in p["flags"]) for p in picks))


# --- The whole prepare stage, with stand-ins ---

@check
def a_recording_under_15_s_is_skipped_with_a_plain_line():
    """launch-fixes: launch night had a 14.9 s recording. prepare() leaves a recording under 15 s out before
    calibrating or indexing it, says so in one plain line and in candidates.json's "skipped", and goes on with the
    rest; when every new recording is that short it stops with a plain reason"""
    def run(tmp, short_ones):
        tmp = Path(tmp)
        inp, shorts, lib = tmp / "input", tmp / "shorts", tmp / "library"
        for d in (inp, shorts, lib):
            d.mkdir(exist_ok=True)
        (lib / "library.json").write_text(json.dumps({"videos": {}}), encoding="utf-8")
        for name in ("GTA_20261119000000.webm", "GTA_20261119010000.webm"):
            (inp / name).write_bytes(b"x")
        guided_run, _ = stand_in_guided(["PASS"])
        asked, lines = [], []

        def index_video(video, profile, every, library, cache_dir, log):
            asked.append(Path(video).stem)
            return made_up_idx(Path(video).stem, video)
        old = launch.probe_duration
        launch.probe_duration = lambda v: 14.9 if Path(v).stem in short_ones else DURATION
        try:
            folder = launch.prepare("gta6", input_dir=inp, shorts_dir=shorts, library_dir=lib, day="2026-11-19",
                                    log=lines.append, index_video=index_video, guided_run=guided_run,
                                    clip_runner=lambda v, p: None, check=False, sheets=False, safety=None,
                                    posted_file=Path(tmp) / "posted_topics.json")   # none posted
        finally:
            launch.probe_duration = old
        return json.loads((folder / "candidates.json").read_text(encoding="utf-8")), asked, lines
    with tempfile.TemporaryDirectory() as tmp:
        data, asked, lines = run(tmp, {"GTA_20261119000000"})
    stopped = None
    with tempfile.TemporaryDirectory() as tmp:
        try:
            run(tmp, {"GTA_20261119000000", "GTA_20261119010000"})
        except launch.LaunchError as e:
            stopped = str(e)
    why = data["skipped"].get("GTA_20261119000000.webm", "")
    return (asked == ["GTA_20261119010000"] and "14.9 s" in why and "15 s or more" in why
            and any(l.strip().startswith("Skipped GTA_20261119000000.webm: only 14.9 s") for l in lines)
            and [r["name"] for r in data["recordings"]] == ["GTA_20261119010000.webm"]
            and stopped and "under 15 s" in stopped)


@check
def prepare_writes_candidates_with_evidence_and_logs_the_run():
    """prepare() on two made-up recordings: calibrates with the stand-in, indexes both, sends the one whose HUD
    disagrees with the model to NO-HUD, mines, picks, types and writes candidates.json; _launch_runs.json says
    'prepared'; a dry run writes no runs log and reads no used footage"""
    def run(tmp, dry):
        tmp = Path(tmp)
        inp, shorts, lib = tmp / "input", tmp / "shorts", tmp / "library"
        for d in (inp, shorts, lib):
            d.mkdir(exist_ok=True)
        (lib / "library.json").write_text(json.dumps({"videos": {}}), encoding="utf-8")
        vids = [inp / "GTA_20261119000000.webm", inp / "GTA_20261119010000.webm"]
        for v in vids:
            v.write_bytes(b"x")
        footage_short.save_used(shorts, "earlier", [["GTA_20261119000000", 90.0, 170.0]])
        guided_run, _ = stand_in_guided(["PASS"])
        asked = []

        def index_video(video, profile, every, library, cache_dir, log):
            asked.append((Path(video).stem, profile))
            idx = made_up_idx(Path(video).stem, video)
            if Path(video).stem.endswith("010000") and not profile.endswith("_nohud"):
                blind = made_up_timeline(made_up_counts(), no_minimap=((0, 1200),))
                blind["on"] = True
                idx["_timeline"] = blind
            if profile.endswith("_nohud"):
                idx.pop("_timeline")
                idx["profile"]["name"] = profile
            return idx

        old = launch.probe_duration
        launch.probe_duration = lambda v: DURATION
        try:
            folder = launch.prepare("gta6", videos=[str(v) for v in vids] if dry else None, dry_run=dry,
                                    input_dir=inp, shorts_dir=shorts, library_dir=lib, day="2026-11-19",
                                    log=lambda m: None, index_video=index_video, guided_run=guided_run,
                                    clip_runner=lambda v, p: None, check=False, sheets=False, safety=None,
                                    posted_file=Path(tmp) / "posted_topics.json")   # none posted: the owner's real list changes
        finally:
            launch.probe_duration = old
        data = json.loads((folder / "candidates.json").read_text(encoding="utf-8"))
        return data, asked, launch.load_runs(shorts)["runs"], folder

    def real(tmp):
        return run(tmp, False)

    def dry(tmp):
        return run(tmp, True)
    old_env = launch.os.environ.get("GTA_CLIPPER_PROFILES")
    try:
        data, asked, runs, folder = with_profile_dirs(real)
        ddata, _, druns, dfolder = with_profile_dirs(dry)
    finally:
        if old_env is None:
            launch.os.environ.pop("GTA_CLIPPER_PROFILES", None)
        else:
            launch.os.environ["GTA_CLIPPER_PROFILES"] = old_env
    modes = {r["stem"]: r["mode"] for r in data["recordings"]}
    picks = data["picks"]
    first_used = [p for p in picks if p["video"] == "GTA_20261119000000" and p["window"][0] < 170
                  and p["window"][1] > 90]
    return (folder.name == "launch-2026-11-19" and modes == {"GTA_20261119000000": "hud", "GTA_20261119010000": "nohud"}
            and ("GTA_20261119010000", "gta6_nohud") in asked and data["calibration"]["mode"] == "hud"
            and 1 <= len(picks) <= 3 and all(p["type"] in ("action", "narrated") and p["evidence"] for p in picks)
            and not first_used and runs[-1]["status"] == "prepared"
            and all("NO-HUD" in " ".join(p.get("flags", [])) for p in picks if p["mode"] == "nohud")
            and dfolder.name == "launch-2026-11-19-dryrun" and druns == [] and ddata["dry_run"]
            and any(p["video"] == "GTA_20261119000000" and p["window"][0] < 170 and p["window"][1] > 90
                    for p in ddata["picks"]))


@check
def a_frame_sheet_shows_the_moment_with_its_times():
    """moment_sheet() writes one JPEG of the footage, a frame every 2 s with the recording's time on each"""
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "src.mp4", Path(tmp) / "sheet.jpg"
        made = launch.subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=20",
                                      "-pix_fmt", "yuv420p", str(src)], capture_output=True)
        if made.returncode:
            return False
        ok = launch.moment_sheet(src, 4.0, 12.0, out)
        size = launch.subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of",
                                      "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
        return ok and size == "1600,226"



@check
def one_heavy_job_at_a_time():
    """launch-fixes: prepare, render and the suites that render take one lock. A second job refuses with a plain line
    naming the holder; a lock whose process is gone (or a reused pid) is taken over; the holder's own process and its
    child processes (they inherit GTA_HEAVY_JOB) pass; release removes only its own lock"""
    import heavy
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "lock.json"
        live = {111: 1000.0, 222: 1000.0}
        alive = lambda pid: live.get(pid)
        env_a, env_b = {}, {}
        tok = heavy.acquire("launch.py prepare", lock, alive, pid=111, env=env_a)
        try:
            heavy.acquire("storytest.py", lock, alive, pid=222, env=env_b)
            refused = False
        except heavy.Busy as e:
            refused = "launch.py prepare" in str(e) and "111" in str(e)
        nested = heavy.acquire("render inside", lock, alive, pid=111, env={}) is None
        child = heavy.acquire("explainer.py", lock, alive, pid=333, env={heavy.ENV: tok}) is None
        heavy.release("not-mine", lock, env={})
        kept = lock.exists()
        heavy.release(tok, lock, env=env_a)
        freed = not lock.exists() and heavy.ENV not in env_a
        tok2 = heavy.acquire("first", lock, alive, pid=111, env={})
        del live[111]                                       # the holder died without releasing
        stale = heavy.acquire("second", lock, alive, pid=222, env={}) is not None
        later = lambda pid: 5000.0                          # pid 222 reused by a process started after the lock
        reused = heavy.holder(lock, alive) is not None and heavy.holder(lock, later) is None
        heavy.acquire("third", lock, later, pid=444, env={})
        data = json.loads(lock.read_text(encoding="utf-8"))
        line = heavy.ram_line(12.34) == "Free memory: 12.3 GB" and heavy.ram_line(None).startswith("Free memory:")
    return (tok and refused and nested and child and kept and freed and tok2 and stale and data["what"] == "third"
            and reused and line)


@check
def a_stopped_prepare_uses_the_finished_clip_runs_again():
    """launch-fixes: prepare carries on where it stopped; a clip.py run that finished in the run folder (for the same
    recording and profile) is used again and clip.py is not run once more; an unfinished one, or another profile's,
    is not"""
    with tempfile.TemporaryDirectory() as tmp:
        out, logs, video = Path(tmp) / "clip_runs", Path(tmp) / "logs", Path(tmp) / "rec_a.webm"
        done_run = out / "rec_a" / "20261119-180131"
        half_run = out / "rec_a" / "20261119-190000"
        done_run.mkdir(parents=True)
        half_run.mkdir(parents=True)
        (done_run / "scores.json").write_text("{}", encoding="utf-8")
        launch.write_json(done_run / launch.CLIP_DONE, {"profile": "gta6"})
        ran = []
        saved = launch.subprocess.run
        launch.subprocess.run = lambda *a, **k: ran.append(a) or SimpleNamespace(returncode=1, stdout="")
        try:
            again = launch.run_clip(video, "gta6", out, logs)
            other = launch.finished_clip_run(video, "gta6_nohud", out)
            none_yet = launch.finished_clip_run(Path(tmp) / "rec_b.webm", "gta6", out)
        finally:
            launch.subprocess.run = saved
    return again == done_run and not ran and other is None and none_yet is None



@check
def a_short_no_longer_in_shorts_json_leaves_the_review_page():
    """launch-fixes: render keeps earlier render.json entries (so --short N keeps the others), but only for Shorts
    still in shorts.json; a renumbered Short's old FAILED entry is dropped"""
    old = {"shorts": [{"short": 1, "status": "READY"}, {"short": 3, "status": "FAILED"}]}
    kept = launch.kept_results(old, {"shorts": [{"short": 1}, {"short": 4}]})
    return list(kept) == [1] and launch.kept_results(None, {"shorts": [{"short": 1}]}) == {}



@check
def spoken_swearing_in_the_first_7_s_is_found():
    """launch-fixes: speech.swearing (faster-whisper's words, stood in here) finds the subtitle check's swear words in
    what is said: before 7 s they are "early" (the Short fails), later ones are flags; mild words pass; a speech check
    that breaks gives None (the Short keeps its "listen once" flag)"""
    import speech
    said = [(0.5, 0.8, "Hey"), (2.0, 2.3, "shit,"), (4.0, 4.2, "damn"), (9.1, 9.4, "fuck"), (12.0, 12.2, "nice")]
    got = speech.swearing("x.mp4", transcribe=lambda p: said)
    clean = speech.swearing("x.mp4", transcribe=lambda p: [(1.0, 1.2, "hello")])

    def broken(path):
        raise RuntimeError("no model")
    none = launch.speech_check("x.mp4", log=lambda m: None, check=broken)
    return (got["words"] == 5 and [w for _, w in got["early"]] == ["shit"] and [w for _, w in got["later"]] == ["fuck"]
            and not clean["early"] and not clean["later"] and none is None), got



@check
def the_preflight_unloads_our_own_model_before_it_measures_memory():
    """launch-fixes: with the memory floor at 7 GB, a qwen3-vl left loaded by a render minutes ago (keep_alive) could
    make /launch refuse with "close other programs"; the preflight now unloads it (and any other model) first"""
    unloaded, saved = [], (launch.vlm.check_ollama, launch.vlm.unload, launch.index.free_ram_gb)
    launch.vlm.check_ollama = lambda m: {"loaded": [launch.index.MODEL, "other:1b"], "digest": "x", "capabilities": []}
    launch.vlm.unload = unloaded.append
    launch.index.free_ram_gb = lambda: 12.0 if launch.index.MODEL in unloaded else 6.5
    try:
        free = launch.preflight(launch.index.MODEL, log=lambda m: None)
    finally:
        launch.vlm.check_ollama, launch.vlm.unload, launch.index.free_ram_gb = saved
    return free == 12.0 and set(unloaded) == {launch.index.MODEL, "other:1b"}


@check
def running_out_of_memory_in_the_safety_checks_stops_with_a_plain_error():
    """launch-fixes: the safety step's memory stop is a SafetyError, so launch.py prints ERROR: and exits 2 (the run
    stays "preparing" and carries on next time) instead of a traceback"""
    return issubclass(safety.MemoryError_, safety.SafetyError)


def main():
    ok = True
    for fn in CHECKS:
        try:
            got = fn()
            good, err = bool(got), ""
        except Exception as e:
            good, err = False, f"  ({type(e).__name__}: {e})"
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'} {fn.__name__}: {' '.join((fn.__doc__ or '').split())}{err}", flush=True)
    print("RESULT: " + ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    import heavy                          # one heavy job at a time (launch-fixes)
    heavy.main_guard("launchtest.py")
    main()
