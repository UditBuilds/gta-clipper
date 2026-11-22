"""Checks for PR 29's picker variety (launch.py pick_varied and repick): payoff strength first, no near-duplicates, at
most 2 picks a recording, both types in a run, posted topics cost points, and the re-pick of an old run on its cached
data. The Nov 20 case is rebuilt from that night's pool: three "stars appear" moments of one recording were picked
while the motorbike crash waited for a --count 6 re-run. No Ollama, no footage.

  .venv\\Scripts\\python.exe varietytest.py
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import launch
from launchtest import made_up_idx

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def mo(mid, video, start, score, category, terms, topic=None, point=None, profile="gta6"):
    m = {"id": mid, "video": video, "start_s": float(start), "end_s": float(start) + 30.0, "session_s": float(start),
         "score": score, "category": category, "terms": dict(terms), "profile": profile, "path": f"{video}.webm"}
    if topic:
        m["topic"] = {"id": topic, "why": "made up", "at_s": start + 10.0}
    if point is not None:
        m["point"] = point
    return m


A, B, C = "rec_135610", "rec_134026", "rec_143644"


def nov20_pool():
    """The candidates that passed every check on Nov 20 (launch-2026-11-20-part2's pool: scores, categories and
    verified terms as saved), with what the frames said where it matters."""
    star = {"star_rise": 2.5, "stars": 1.0, "activity": 2.0, "activity_label": "chase", "topic": 1.5}
    return [
        mo("M1", A, 722.5, 9.64, "star rise", dict(star, hud6=1.0), "wanted-system"),
        mo("M5", A, 401.5, 8.55, "star rise", star, "wanted-system"),
        mo("M8", A, 316.5, 8.47, "star rise", star, "wanted-system"),
        mo("M15", B, 772.15, 6.05, "chase", {"stars": 1.0, "activity": 2.0, "activity_label": "chase", "police": 0.5,
                                              "topic": 1.5}, "police-chase"),
        mo("M19", C, 310.5, 4.92, "shootout", {"activity": 2.0, "activity_label": "shootout", "topic": 1.5}, "big-chases"),
        mo("M25", B, 460.5, 3.43, "crash", {"action": 0.94, "crash": 1.5, "activity": 0.5, "activity_label": "driving"}),
        mo("M27", A, 461.5, 3.36, "crash", {"action": 0.9, "crash": 1.5}),
        mo("M34", "rec_165142", 570.5, 2.36, "action", {"action": 1.2}),
    ]


TEXT = {"M25": "motorcyclist rides through city streets, crashes into car. player rides motorbike",
        "M27": "player drives truck on highway bridge. player falls and lies on ground after a crash"}


def text_of(m):
    return TEXT.get(m["id"], "")


def posted(as_of=None):
    """The owner's list (posted_topics.json in the repo), as launch.load_posted reads it."""
    return launch.load_posted(launch.POSTED_FILE, as_of, log=lambda s: None)


def quiet(*_):
    return None


@check
def the_nov20_case_puts_the_bike_crash_on_top_and_no_near_duplicates():
    """Nov 20 again: the old way (score only) picks M1, M5, M8, three star rises of one recording; now the bike crash
    (M25) is in the top 3, at most 2 picks come from one recording, no two picks of a recording are the same kind
    within 10 min, and the run has an action clip and a topic"""
    pool = nov20_pool()
    old = [m["id"] for m in sorted(pool, key=lambda m: -m["score"])[:3]]
    # The list as it was that night, without the Nov 21 type rule (no crash after a posted crash: the black SUV was
    # posted that day, so M25 waits now; the_crash_rule_holds_for_real_moment_labels checks that rule)
    then = [dict(t, type=None) for t in posted(as_of="2026-11-20T15:06:03")]
    picks = launch.pick_varied([dict(m) for m in pool], 3, then, text_of, quiet)
    ids = [m["id"] for m in picks]
    per = {}
    for m in picks:
        per[m["video"]] = per.get(m["video"], 0) + 1
    dup = any(launch.near_duplicate(m, p) for m in picks for p in picks if m is not p)
    return (old == ["M1", "M5", "M8"] and "M25" in ids and max(per.values()) <= 2 and not dup
            and any(launch.can_be("action", m) for m in picks) and any(launch.can_be("topic", m) for m in picks)), ids


@check
def payoff_strength_ranks_a_clear_event_first():
    """an impact the frames confirmed is 3, a star rise with police or a shootout on screen 2, a HUD change alone or
    action with no single event 1, quiet 0; a crash the safety answers didn't see (verify_terms dropped it) is no
    impact; a clear impact beats a higher-scoring quiet moment"""
    s = launch.payoff_strength
    crash = mo("a", A, 0, 2.0, "crash", {"crash": 1.5})
    cause = mo("b", A, 900, 8.0, "star rise", {"star_rise": 2.5, "police": 0.5})
    alone = mo("c", A, 1800, 9.0, "star rise", {"star_rise": 2.5})
    drive = mo("d", B, 0, 9.5, "action", {"action": 1.0})
    dropped = mo("e", B, 900, 3.0, "action", {"action": 1.0}, point={"action_payoff": [], "formats": []})
    picks = launch.pick_varied([dict(crash), dict(drive)], 1, (), None, quiet)
    return ([s(x)[0] for x in (crash, cause, alone, drive)] == [3, 2, 1, 0] and s(dropped)[0] == 0
            and picks[0]["id"] == "a" and "payoff 3/3" in picks[0]["pick_why"])


@check
def posted_topics_cost_points_and_match_only_their_own_moments():
    """a star rise repeats the owner's strong topics ("wanted stars" / "stars appear", "crime reported": -4); the
    motorbike crash matches "motorbike crash (Short 6)" (-2) but not "black SUV crash on the bridge", and the truck
    crash on a bridge matches neither; topics added after a run are left out when that run is re-picked"""
    now, then = posted(), posted(as_of="2026-11-20T15:06:03")
    pool = {m["id"]: m for m in nov20_pool()}
    star = launch.topic_repeats(pool["M1"], now, text_of(pool["M1"]))
    bike = launch.topic_repeats(pool["M25"], now, text_of(pool["M25"]))
    truck = launch.topic_repeats(pool["M27"], now, text_of(pool["M27"]))
    bike_then = launch.topic_repeats(pool["M25"], then, text_of(pool["M25"]))
    names = {t["name"] for t in now}
    return (star[0] == launch.STRONG_REPEAT_COST and "wanted stars" in star[1] and "crime reported" in star[1]
            and bike == (launch.REPEAT_COST, ["motorbike crash (Short 6)"]) and truck == (0.0, [])
            and bike_then == (0.0, []) and {"motorbike crash (Short 6)", "tree effigy / passing out",
                                             "crime needs a witness"} <= names
            and len(now) - len(then) == 3), (star, bike, truck)


@check
def the_posted_list_is_read_only_and_checked():
    """load_posted never writes: a missing list means no costs (said in the log); a rule with an unknown key is an
    error with the topic's name"""
    with tempfile.TemporaryDirectory() as tmp:
        said = []
        path = Path(tmp) / "posted_topics.json"
        none = launch.load_posted(path, log=said.append)
        wrote = list(Path(tmp).iterdir())
        path.write_text(json.dumps({"topics": [{"name": "x", "rules": [{"colour": "red"}]}]}),
                                                      encoding="utf-8")
        try:
            launch.load_posted(path, log=quiet)
            bad = None
        except launch.LaunchError as e:
            bad = str(e)
    return none == [] and not wrote and said and "No posted-topics list" in said[0] and bad and "'x'" in bad


@check
def two_picks_of_one_recording_must_differ_and_three_only_when_nothing_else_qualifies():
    """the same kind within 10 min of a pick is a near-duplicate, never picked; a different kind, or the same kind
    far apart, may be; a third pick of a recording comes only when no other recording has a free candidate (and says
    so)"""
    one = [mo("s1", A, 100, 9.0, "star rise", {"star_rise": 2.5}), mo("s2", A, 400, 8.9, "star rise", {"star_rise": 2.5}),
           mo("c1", A, 900, 3.0, "crash", {"crash": 1.5}), mo("s3", A, 1300, 8.0, "star rise", {"star_rise": 2.5})]
    picks = launch.pick_varied([dict(m) for m in one], 3, (), None, quiet)
    ids = [m["id"] for m in picks]
    other = one + [mo("o1", B, 50, 1.0, "action", {"action": 1.0})]
    capped = launch.pick_varied([dict(m) for m in other], 3, (), None, quiet)
    left = {m["id"]: m for m in other}
    return (sorted(ids) == ["c1", "s1", "s3"] and "s2" not in ids
            and any("nothing else qualified" in m["pick_why"] for m in picks)
            and sorted(m["id"] for m in capped) == ["c1", "o1", "s1"]), (ids, [m["id"] for m in capped], left["s2"])


@check
def a_run_gets_both_an_action_clip_and_a_topic():
    """when the best candidates are all crashes, the weakest one makes way for the best topic that passed (and says
    so); when they are all topics, the best impact comes in"""
    crashes = [mo(f"c{k}", f"r{k}", 0, 5.0 - k, "crash", {"crash": 1.5}) for k in range(3)]
    topic = mo("t", "rt", 0, 2.0, "star rise", {"star_rise": 2.5, "topic": 1.5}, "wanted-system")
    a = launch.pick_varied([dict(m) for m in crashes + [topic]], 3, (), None, quiet)
    topics = [mo(f"t{k}", f"q{k}", 0, 9.0 - k, "star rise", {"star_rise": 2.5}, "wanted-system") for k in range(3)]
    crash = mo("c", "rc", 0, 1.0, "crash", {"crash": 1.5})
    b = launch.pick_varied([dict(m) for m in topics + [crash]], 3, (), None, quiet)
    swapped = next(m for m in a if m["id"] == "t")
    return ({m["id"] for m in a} == {"c0", "c1", "t"} and "so the run has a topic Short" in swapped["pick_why"]
            and "c" in {m["id"] for m in b})


@check
def every_pick_says_why_it_beat_the_others():
    """each pick's pick_why gives its payoff tier and points and the candidate it beat; a candidate left out says
    why (a near-duplicate, its recording full, or ranked lower)"""
    pool = [dict(m) for m in nov20_pool()]
    picks = launch.pick_varied(pool, 3, posted(as_of="2026-11-20T15:06:03"), text_of, quiet)
    left = [m for m in pool if m not in picks]
    return (all("payoff" in m["pick_why"] and "points" in m["pick_why"] for m in picks)
            and any("beat " in m["pick_why"] for m in picks) and all(m.get("not_picked_why") for m in left))


@check
def the_used_footage_is_read_as_of_the_run():
    """repick sees the used footage as it was when the run was prepared: the Shorts that run (or its -part2) made, and
    every one after, are left out"""
    with tempfile.TemporaryDirectory() as tmp:
        used = {"shorts": [{"name": "old", "spans": [["a", 1.0, 2.0]]},
                           {"name": "6-bike@launch-2026-11-20-part2", "spans": [["b", 3.0, 4.0]]},
                           {"name": "effigy@manual", "spans": [["c", 5.0, 6.0]]}]}
        (Path(tmp) / "_used_footage.json").write_text(json.dumps(used), encoding="utf-8")
        then = launch.used_as_of(tmp, "launch-2026-11-20")
        part2 = launch.used_as_of(tmp, "launch-2026-11-20-part2")
        later = launch.used_as_of(tmp, "launch-2026-11-21")
    return then == [("a", 1.0, 2.0)] and part2 == then and len(later) == 3


def hud6_stub(path, a, b, prof, **kw):
    return []


@check
def repick_runs_on_cached_data_and_leaves_the_run_alone():
    """launch.repick on a made-up run: reads each recording's cached index.json (nothing indexed), writes repick.json
    and repick.txt with the old and the new picks (each new one with its payoff and why) in a new folder, and changes
    neither the run's candidates.json nor _used_footage.json"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        lib = tmp / "library"
        stems = ["rec_a", "rec_b"]
        for k, stem in enumerate(stems):
            idx = made_up_idx(stem, tmp / f"{stem}.webm")
            (lib / stem).mkdir(parents=True)
            (lib / stem / "index.json").write_text(json.dumps(idx), encoding="utf-8")
        run = tmp / "launch-2026-11-20"
        run.mkdir()
        cand = {"game": "gta6", "dry_run": True, "count": 2, "created": "2026-11-20T15:06:03",
                "settings": {"library": str(lib)},
                "recordings": [{"stem": s, "path": str(tmp / f"{s}.webm"), "mode": "hud", "clip_run": None,
                                "session_offset_s": 600.0 * k} for k, s in enumerate(stems)],
                "picks": [{"id": "M1", "video": "rec_a", "start_s": 100.0, "end_s": 130.0, "score": 5.0,
                           "category": "star rise", "terms": {"star_rise": 2.5}}]}
        (run / "candidates.json").write_text(json.dumps(cand), encoding="utf-8")
        (tmp / "_used_footage.json").write_text(json.dumps({"shorts": []}), encoding="utf-8")
        before = {p: hashlib.sha1(p.read_bytes()).hexdigest() for p in (run / "candidates.json", tmp / "_used_footage.json")}
        data = launch.repick(run, shorts_dir=tmp, safety=lambda m, rec, folder: (True, []), log=quiet, hud6=hud6_stub)
        out = tmp / "launch-2026-11-20-repick"
        after = {p: hashlib.sha1(p.read_bytes()).hexdigest() for p in before}
        text = (out / "repick.txt").read_text(encoding="utf-8")
        return (before == after and (out / "repick.json").is_file() and "OLD PICKS" in text and "NEW PICKS" in text
                and data["old_picks"][0]["id"] == "M1" and data["new_picks"]
                and all(p["pick_why"] and p["payoff_strength"] for p in data["new_picks"])), data["new_picks"]


def made_up_posted(topics):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "posted_topics.json"
        path.write_text(json.dumps({"topics": topics}), encoding="utf-8")
        return launch.load_posted(path, log=quiet)


@check
def no_crash_right_after_a_posted_crash_unless_nothing_else_meets_the_bar():
    """the owner's rule (Nov 21): with a crash among the last 2 posted Shorts, a crash isn't picked while another
    candidate passed every check (the pool's payoff bar), a quiet 1/3 one too; with nothing else left the crash is
    picked and says why, but only one crash a run while anything else is free; a planned topic is not one of the
    last 2 posted"""
    posted = made_up_posted([
        {"name": "old star", "type": "star rise", "posted": "2026-11-13", "rules": [{"words": ["zzz"]}]},
        {"name": "bike crash", "type": "crash", "posted": "2026-11-20", "rules": [{"words": ["zzz"]}]},
        {"name": "effigy", "type": "discovery", "posted": "2026-11-21", "rules": [{"words": ["zzz"]}]},
        {"name": "witness", "type": "star rise", "planned": "2026-11-21", "rules": [{"words": ["zzz"]}]}])
    crash = mo("c", A, 0, 9.0, "crash", {"crash": 1.5})
    cause = mo("s", B, 0, 3.0, "star rise", {"star_rise": 2.5, "police": 0.5})
    quiet_one = mo("q", B, 0, 3.0, "chase", {"activity": 2.0, "activity_label": "chase"})
    held = launch.pick_varied([dict(crash), dict(cause)], 1, posted, None, quiet)
    quiet_wins = launch.pick_varied([dict(crash), dict(quiet_one)], 1, posted, None, quiet)
    allowed = launch.pick_varied([dict(crash)], 1, posted, None, quiet)
    crash2 = mo("c2", "rec_x", 0, 8.0, "crash", {"crash": 1.5})
    one = launch.pick_varied([dict(crash), dict(crash2), dict(quiet_one)], 2, posted, None, quiet)
    if sorted(m["id"] for m in one) != ["c", "q"]:          # the fallback lets one crash through, not two
        return False, [m["id"] for m in one]
    free = launch.pick_varied([dict(crash), dict(cause)], 1, posted[:1], None, quiet)
    recent = launch.recent_posted(posted)
    return (held[0]["id"] == "s" and quiet_wins[0]["id"] == "q" and allowed[0]["id"] == "c"
            and "no other type is left" in allowed[0]["pick_why"]
            and free[0]["id"] == "c" and recent == [("crash", "bike crash"), ("discovery", "effigy")]), (
        [m["id"] for m in held], [m["id"] for m in quiet_wins], [m["id"] for m in allowed], recent)


@check
def the_crash_rule_holds_for_real_moment_labels():
    """the owner (Nov 21): today's re-pick put M27 (the truck on the bridge, its label "crash" with verified terms)
    first, right after the posted motorbike crash, while M1 and M26 were there. With the posted list written as the
    owner writes it and the moments as the miner saved them (launch-2026-11-20-part2's pool), M27 is held back and
    says why; any label naming a crash ("truck crash", "topic: car crashes", an action payoff "truck crash") is a
    crash, and so is a posted type written "Bike crash"."""
    posted = made_up_posted([
        {"name": "black SUV crash on the bridge", "type": "crash", "posted": "2026-11-20",
         "rules": [{"category": ["crash"]}]},
        {"name": "motorbike crash (Short 6)", "type": "Bike crash", "posted": "2026-11-20",
         "added": "2026-11-20T23:03:42", "rules": [{"words": ["motorbike", "motorcycle"]}]},
        {"name": "tree effigy / passing out", "type": "discovery", "posted": "2026-11-21", "rules": [{"words": ["effigy"]}]},
        {"name": "crime needs a witness", "type": "star rise", "planned": "2026-11-21",
         "rules": [{"words": ["witness"]}]}])
    m27 = mo("M27", A, 461.5, 3.36, "crash", {"action": 0.87, "activity": 0.5, "activity_label": "driving",
                                             "crash": 1.5, "confidence": 0.49})
    m1 = mo("M1", A, 722.5, 9.64, "star rise", {"action": 0.77, "star_rise": 2.5, "stars": 2.0, "activity": 2.0,
                                               "activity_label": "chase", "confidence": 0.47, "topic": 1.5, "hud6": 1.0,
                                               "dialogue": -0.6}, "wanted-system")
    m34 = mo("M34", "rec_165142", 570.5, 2.36, "action", {"action": 1.37, "activity": 0.5, "activity_label": "driving",
                                                          "confidence": 0.49})
    picks = launch.pick_varied([dict(m27), dict(m1), dict(m34)], 2, posted, text_of, quiet)
    left = launch.pick_varied([m for m in [dict(m27), dict(m1), dict(m34)]], 3, posted, text_of, quiet)
    m27_left = next(m for m in left if m["id"] == "M27")
    labels = [launch.moment_type(mo("x", A, 0, 1.0, "truck crash", {})),
              launch.moment_type(mo("x", A, 0, 1.0, "topic: car crashes", {})),
              launch.moment_type(mo("x", A, 0, 1.0, "chase", {}, point={"action_payoff": ["truck crash"]}))]
    recent = launch.recent_posted(posted)
    return (sorted(m["id"] for m in picks) == ["M1", "M34"]
            and "motorbike crash (Short 6)" in next(m for m in [dict(m27)] + picks if m["id"] == "M1")["pick_why"]
            and labels == ["crash", "crash", "crash"] and recent[0][0] == "crash"
            and "M27" in [m["id"] for m in left] and "no other type is left" in m27_left["pick_why"]), (
        [m["id"] for m in picks], labels, recent, [(m["id"], m.get("pick_why")) for m in left])


@check
def a_moment_that_failed_its_shot_check_at_render_is_not_picked_again():
    """M21 of the Nov 20 re-pick was part2's Short 4 again (the jet ski over an empty ocean, refused by the shot check
    at render): failed_renders reads every run's render.json, and a moment mostly inside a failed Short's window is
    left out before the pool, with why; a READY Short, or one that failed for another reason, leaves its footage be"""
    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp) / "launch-2026-11-20-part2"
        run.mkdir()
        shorts = [{"short": 4, "status": "FAILED", "video": B, "window": [769.15, 804.15],
                   "checks": {"rejected_shots": [{"why": ["only the character's back in an empty place"]}]}},
                  {"short": 5, "status": "FAILED", "video": B, "window": [100.0, 130.0], "problems": ["a line runs on"]},
                  {"short": 6, "status": "READY", "video": B, "window": [457.0, 477.0]}]
        (run / "render.json").write_text(json.dumps({"shorts": shorts}), encoding="utf-8")
        failed = launch.failed_renders(tmp)
    m21 = mo("M21", B, 784.5, 4.56, "chase", {"activity": 2.0})
    m25 = mo("M25", B, 460.5, 3.43, "crash", {"crash": 1.5})
    m_text = mo("Mx", B, 105.0, 3.0, "action", {"action": 1.0})
    hit = launch.failed_before(m21, failed)
    return (len(failed) == 1 and hit and hit["where"] == "launch-2026-11-20-part2 Short 4" and "empty place" in hit["why"]
            and launch.failed_before(m25, failed) is None and launch.failed_before(m_text, failed) is None)


def main():
    ok = True
    for fn in CHECKS:
        try:
            got = fn()
            good, err = (bool(got[0]), f"  (got {got[1]})") if isinstance(got, tuple) else (bool(got), "")
            err = err if not good else ""
        except Exception as e:
            good, err = False, f"  ({type(e).__name__}: {e})"
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'} {fn.__name__}: {' '.join((fn.__doc__ or '').split())}{err}", flush=True)
    print(f"RESULT: {'PASS' if ok else 'FAIL'} ({len(CHECKS)} checks)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
