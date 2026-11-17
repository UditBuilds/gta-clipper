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

import calibrate as cal
import clip
import footage_short
import index
import launch
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
        calls.append((name, Path(video).name, slots, serve))
        if result is None:
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
    """no profile: guided calibration with auto_accept on the longest recording; PASS uses it; stars missing tries the
    next recording; FAIL or a run that can't start means NO-HUD; a finished profile is used as it is, unless its last
    guided check FAILED"""
    durations = {"short": 100.0, "long": 900.0, "mid": 400.0}
    vids = [Path(f"{s}.webm") for s in durations]

    def case(results, existing=None):
        def inner(tmp):
            if existing:
                existing(tmp)
            run, calls = stand_in_guided(results)
            got = launch.choose_profile("gta6", "gta6", vids, durations, 6, log=lambda m: None, guided_run=run)
            return got, calls
        return with_profile_dirs(inner)

    ok_run, calls = case(["PASS"])
    stars_later, calls2 = case(["PASS for the minimap. STARS NOT CALIBRATED", "PASS"])
    never_stars, _ = case(["PASS for the minimap. STARS NOT CALIBRATED"] * 3)
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
            and calls[0][1] == "long.webm" and calls[0][2] == 6 and calls[0][3] is launch.guided.auto_accept
            and stars_later["stars"] and [c[1] for c in calls2] == ["long.webm", "mid.webm"]
            and never_stars["mode"] == "hud" and not never_stars["stars"] and len(never_stars["attempts"]) == 3
            and failed["mode"] == "nohud" and failed["profile"] == "gta6_nohud"
            and cannot["mode"] == "nohud" and kept["mode"] == "hud" and calls3 == []
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


# --- The whole prepare stage, with stand-ins ---

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
                                    clip_runner=lambda v, p: None, check=False, sheets=False)
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
    main()
