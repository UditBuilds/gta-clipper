"""Checks for the content bank (PR 32: bank.py, and what launch.py and footage_short.py do with it): everything mined and
kept, labels from the peak, shelves, statuses, footage locked only when it is posted, the migration, the review page and
the picker. No Ollama: the indexes, the vision model's answers and the sounds are made up here (launchtest's made-up
recording), or copied from the real M24.

  .venv\\Scripts\\python.exe banktest.py
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import bank
import footage_short
import index
import launch
import launchtest as lt
import library
import safety
import testkit
import truth
from footagetest import made_up_index, made_up_timeline, seg

HERE = Path(__file__).resolve().parent
CHECKS = []
# The owner's posted list as it was on Nov 23 (a copy): the next posted Short must change no check here.
launch.POSTED_FILE = HERE / "testdata" / "posted_topics_2026-11-23.json"


def check(fn):
    CHECKS.append(fn)
    return fn


def quiet(*_):
    return None


# --- Made-up recordings ---

def sheets_of(duration, lines=(), activity="driving"):
    """A sheet every 6 s; lines: {second: (text, activity)}."""
    lines = dict(lines)
    out = []
    for k in range(int(duration // 6)):
        t = k * 6.0
        text, act = lines.get(t, (f"Player drives down a city street", activity))
        out.append({"t": t, "file": f"frames/{index.frame_name(t)}", "error": None, "notable": text, "activity": act,
                    "vehicle": "car", "police_visible": False, "confidence": 0.9, "hud_stars": 0})
    return out


def made_up(stem="GTA_20261119120000", duration=600.0, lines=(), segs=None, counts=None, loud=(), change=(), hud6=(),
            mode="hud"):
    """What bank.load_recs gives for one recording. loud, change: {second: z-score}."""
    segs = segs or [seg(0, duration, "driving")]
    for s in segs:
        s.setdefault("decided_by", "model")
    n = int(duration * 2)
    counts = counts or [0] * n
    idx = made_up_index(stem, segs, duration, made_up_timeline(counts))
    idx["sheets"] = sheets_of(duration, lines)
    idx["profile"] = {"name": "gta6" if mode == "hud" else "gta6_nohud"}
    if mode == "nohud":
        idx.pop("_timeline")
    act = [0.0] * n
    rec = launch.recording_data(stem, idx, act, mode, 0.0)
    L, C = [0.0] * n, [0.0] * n
    for t, v in dict(loud).items():
        L[int(t * 2)] = v
    for t, v in dict(change).items():
        C[int(t * 2)] = v
    rec["raw"] = {"act": act, "loud": L, "change": C, "hud6": {"events": list(hud6)}}
    return rec


def answer(note, **yes):
    return {"note": note, **{k: False for k in safety.WHAT}, **{k: False for k in safety.SEEN}, **yes}


def look(a, b, note, every=1.0, **yes):
    """One saved answer of the safety question for the frames a..b."""
    return {"from_s": a, "to_s": b, "every_s": every, "answer": answer(note, **yes)}


def one(rec, t):
    """The moment of the big kinds whose events hold second t."""
    ms, _ = bank.mine_all(rec)
    return next(m for m in ms if m["family"] == "main" and any(e["t0"] <= t <= e["t1"] for e in m["events"]))


def labelled(rec, t, answers):
    m = one(rec, t)
    sheets, _ = bank.look_at(m, list(answers), None, None)
    bank.set_label(m, rec, sheets)
    m["questions"] = []
    m["shelf"] = bank.shelf_of(m)
    return m


class StandIn:
    """A vision model that answers the safety question with one note, counting how often it is asked. cache_dir: it keeps
    its answers there the way vlm does. only: the stretches it says that about (a plain street elsewhere)."""

    def __init__(self, note="Player drives down a city street", cache_dir=None, only=None, **yes):
        self.calls, self.cached, self.note, self.yes, self.cache_dir, self.model = 0, 0, note, yes, cache_dir, "made up"
        self.only = only

    def look(self, path, a, b, every, work):
        out = []
        for f0, f1, _ in safety.sheet_times(a, b, every):
            self.calls += 1
            here = self.only is None or any(f0 < y and x < f1 for x, y in self.only)
            said = answer(self.note, **self.yes) if here else answer("Player drives down a city street")
            out.append({"from_s": f0, "to_s": f1, "file": "", "answer": said, "error": None})
            if self.cache_dir:
                d = Path(self.cache_dir) / Path(path).stem
                d.mkdir(parents=True, exist_ok=True)
                (d / f"{Path(path).stem}_{f0:.1f}.json").write_text(json.dumps(
                    {"fields": {"video": Path(path).name, "from_ms": round(f0 * 1000), "to_ms": round(f1 * 1000),
                                "every_s": every, "prompt_version": safety.PROMPT_VERSION},
                     "record": {"answer": out[-1]["answer"]}}), encoding="utf-8")
        return out

    def __call__(self, m, rec, folder):
        m["verified"] = {}
        return True, []


# --- 4b. Everything is mined, nothing is capped ---

@check
def every_event_becomes_a_moment_and_nothing_is_capped():
    """12 crashes in a 10-minute recording (the old miner kept at most 4 windows): every event is inside a moment, each
    crash has its own, action moments are 10-30 s, and the calm footage between them is kept too: the moments cover
    nearly all the clean footage, the old mine() a fifth of it"""
    crashes = {30.0 + 48 * k: ("Player's car crashes into a van", "driving") for k in range(12)}
    rec = made_up(lines=crashes)
    ms, left = bank.mine_all(rec)
    main = [m for m in ms if m["family"] == "main"]
    events = bank.events_of(rec)
    inside = all(any(m["start_s"] <= (e["t0"] + e["t1"]) / 2 <= m["end_s"] for m in main) for e in events)
    old = launch.mine(rec)
    return (len(main) == 12 and inside and all(10.0 <= m["end_s"] - m["start_s"] <= 30.0 for m in main)
            and bank.covered_s(ms) > 0.9 * 600 and bank.covered_s(old) < 0.25 * 600 and len(old) == 4
            and all(m["end_s"] - m["start_s"] >= 10.0 - 1e-6 for m in ms if m["family"] == "calm")
            and not any(a is not b and a["video"] == b["video"] and launch.overlap(a["start_s"], a["end_s"], b["start_s"],
                                                                                   b["end_s"]) > 0.01
                        for a in ms for b in ms if a["family"] in ("main", "calm") and b["family"] in ("main", "calm")))


@check
def short_clean_footage_gives_short_moments():
    """a crash in 14 s of clean footage between two cutscenes is a 10-second action moment (the old miner needed 15 s); a
    prompt in 7 s of it a list item of 4 s or more; 3 s of clean footage gives nothing, and that is counted"""
    segs = [seg(0, 100, "cutscene"), seg(100, 114, "driving"), seg(114, 200, "cutscene"), seg(200, 207, "on_foot"),
            seg(207, 300, "cutscene"), seg(300, 303, "driving"), seg(303, 400, "cutscene")]
    rec = made_up(duration=400.0, segs=segs, lines={102.0 + 0: ("x", "driving")})
    rec["sheets"] = [{"t": t, "notable": text, "activity": a, "kinds": sorted(launch.event_kinds(text)),
                      "police_visible": False, "confidence": 0.9}
                     for t, text, a in ((102.0, "Player's car crashes into a wall", "driving"),
                                        (200.0, "Player runs past a workout bench, then buys a tattoo", "on_foot"),
                                        (299.0, "Player's car crashes into a wall", "driving"))]
    ms, left = bank.mine_all(rec)
    crash = [m for m in ms if m["main"] == "crash"]
    small = [m for m in ms if m["main"] == "mechanic"]
    old = launch.windows(rec)
    return (old == [] and len(crash) == 1 and 10.0 - 1e-6 <= crash[0]["end_s"] - crash[0]["start_s"] <= 13.0 + 1e-6
            and len(small) == 1 and 4.0 <= small[0]["end_s"] - small[0]["start_s"] <= 10.0
            and left["no clean footage"] >= 1)


@check
def a_reaction_and_a_find_on_the_same_seconds_are_one_list_item():
    """"Player attacked by a wild boar" is a reaction and a find on the same 6 s: one list item with both kinds, next to the
    fall's own moment; on different seconds they stay two"""
    boar = made_up(lines={300.0: ("Player falls in grass after being attacked by a wild boar.", "on_foot")})
    small = [m for m in bank.mine_all(boar)[0] if m["family"] in bank.SMALL]
    two = made_up(lines={300.0: ("Player argues with a man", "on_foot"), 360.0: ("Player sees a shark", "on_foot")})
    apart = [m for m in bank.mine_all(two)[0] if m["family"] in bank.SMALL]
    return (len(small) == 1 and small[0]["kinds"] == ["discovery", "reaction"] and len(small[0]["events"]) == 2
            and len(apart) == 2 and any(m["main"] == "fall" for m in bank.mine_all(boar)[0]))


@check
def the_new_kinds_come_only_from_what_the_data_shows():
    """a fall, people reacting, a find, a mechanic are read from the lines that say so and not from lines that deny it or
    from scenery; "avoids collision" and a diver's "wreck" are no crash; wanted stars going up, all going, and the heat
    icons changing come from the HUD's own readings, and a NO-HUD recording has none of them; a chase and a shootout from
    the index's segments; nothing has a kind outside the list"""
    k = bank.kinds_in
    words_ok = (k("Men play beach ball, one falls") == {"fall"} and k("Player argues with bar patron") == {"reaction"}
                and k("Player swims with giant manta rays underwater") == {"discovery"}
                and k("Player drives truck with crime reported") == {"mechanic"}
                and k("Player drives, no one falls or fights") == set()
                and k("Blue biplane flies over city at dusk, altitude changing slightly") == set()
                and k("People dance at bar with band performing on stage") == set()
                and "crash" not in k("Player drives pickup, turns left, avoids collision with truck."))
    counts = [0] * 200 + [1] * 60 + [0] * 340 + [0] * 600
    segs = [seg(0, 100, "driving"), seg(100, 130, "chase", stars=1), seg(130, 200, "driving"), seg(200, 220, "shootout"),
            seg(220, 600, "on_foot")]
    wreck = {420.0: ("Diver explores near a wreck", "swimming")}
    icons = [{"kind": "heat", "at_s": 115.0, "before": 2, "after": 1}]
    rec = made_up(segs=segs, counts=counts, lines=wreck, hud6=icons)
    got = {e["kind"] for e in bank.events_of(rec)}
    none = {e["kind"] for e in bank.events_of(made_up(segs=segs, lines=wreck, mode="nohud"))}
    lost = next(e for e in bank.events_of(rec) if e["kind"] == "stars lost")
    return (words_ok and got == {"star rise", "stars lost", "heat icons", "chase", "shooting"} and lost["t0"] == 130.0
            and not none & set(bank.HUD_KINDS) and set(bank.WEIGHT) >= got | set(bank.SMALL) | set(bank.IMPACT)), (got, none)


@check
def the_peak_is_the_loudest_second_round_the_main_event():
    """the loudest half second in the event's own seconds (a louder one elsewhere doesn't count); the picture's biggest
    change where no sound stands out; a HUD change's peak lies within 3 s of it"""
    lines = {300.0: ("Player's car crashes into a wall", "driving")}
    loud = one(made_up(lines=lines, loud={303.5: 1.4, 302.0: 0.6, 320.0: 9.0}), 301)
    seen = one(made_up(lines=lines, loud={302.0: 0.2}, change={304.0: 2.0, 301.0: 1.0}), 301)
    counts = [0] * 400 + [2] * 100 + [0] * 700
    segs = [seg(0, 600, "on_foot")]
    hud = one(made_up(segs=segs, counts=counts, loud={201.5: 1.0, 215.0: 5.0}), 200)
    return ((loud["peak_s"], loud["peak_by"]) == (303.5, "loudest") and (seen["peak_s"], seen["peak_by"]) == (304.0, "most change")
            and hud["main"] == "star rise" and hud["peak_s"] == 201.5)


# --- 4c. Labels from the peak ---

# M24 (_135610, 469-489 s) as it was saved: the index's lines, the safety answers, the loudness and change there.
M24_LINES = {450.0: ("Player drives truck on highway, crashes into debris.", "driving"),
             456.0: ("Player drives truck on highway bridge at sunset.", "driving"),
             468.0: ("Player exits truck, runs away from vehicle on highway bridge.", "on_foot"),
             474.0: ("Player falls and lies on ground after a crash near restaurant.", "on_foot"),
             480.0: ("Player runs past a restaurant with a chicken logo.", "on_foot"),
             486.0: ("Player runs toward and enters a PeeWee's restaurant.", "on_foot")}
M24_LOOKS = [look(461.5, 465.5, "Player drives pickup truck on highway at sunset."),
             look(465.5, 469.5, "Player drives pickup truck on highway with minimap."),
             look(469.5, 473.5, "Player in white pickup truck on bridge with cargo.", crash=True),
             look(473.5, 477.5, "Player runs, jumps, falls, and lies on ground near restaurant."),
             look(477.5, 481.5, "Player character runs past a fallen body near a restaurant."),
             look(481.5, 485.5, "Player runs past a restaurant in sunny outdoor setting.")]
M24_LOUD = {470.0: 0.037, 470.5: 0.121, 471.0: 0.044, 471.5: 0.415, 476.5: 0.623}
M24_CHANGE = {471.0: 1.533, 471.5: 1.257, 472.0: 1.223, 472.5: 1.723, 476.5: 2.453, 477.0: 2.101, 478.0: 3.486,
              478.5: 3.0, 479.5: 6.0}


def m24():
    return made_up("Grand Theft Auto VI_20261119135610", 938.0, M24_LINES, [seg(0, 468, "driving"), seg(468, 938, "on_foot")],
                   loud=M24_LOUD, change=M24_CHANGE)


@check
def m24_is_a_jump_or_fall_never_a_truck_crash():
    """M24 with its saved answers: the old way (verify_terms) keeps "crash", because a car hit the parked pickup at
    472.5 s; the peak is the landing at 476.5 s, whose frames show a jump and a fall and no crash, so its label is "jump
    or fall (stunt)", sure, with no crash and no truck in it, its type is stunt (as the owner posted it), payoff 3/3"""
    rec = m24()
    old = next(m for m in launch.mine(rec) if m["start_s"] <= 474 <= m["end_s"])
    old["verified"] = safety.seen_spans(M24_LOOKS)
    launch.verify_terms(rec, old)
    m = labelled(rec, 476, M24_LOOKS)
    said = (m["label"]["name"] + " " + m["label"]["text"]).lower()
    return (old["category"] == "crash" and "crash" in old["terms"] and m["peak_s"] == 476.5 and m["label"]["kind"] == "fall"
            and m["label"]["sure"] and "truck" not in said and "crash" not in said and "crash" not in m["terms"]
            and m["category"] == "stunt" and launch.moment_type(m) == "stunt" and m["payoff"]["tier"] == 3
            and m["shelf"] == "action" and m["mined_as"] == "crash"), (old["category"], m["label"], m["terms"])


@check
@testkit.needs("gta")
def the_real_m24_is_not_a_truck_crash():
    """the owner's own index, sounds and saved answers of _135610: the moment at 469-489 s is a jump or fall (stunt)"""
    if not (index.LIBRARY / "Grand Theft Auto VI_20261119135610" / "index.json").is_file():
        raise AssertionError("no index of _135610 in the library")
    with tempfile.TemporaryDirectory() as tmp:
        recs = bank.load_recs(bank_dir=tmp, stems={"Grand Theft Auto VI_20261119135610"}, hud6=lambda *a, **k: [], log=quiet)
        ms, _, _, calls = bank.make_moments(recs, None, log=quiet)
    m = next(m for m in ms if m["family"] == "main" and m["start_s"] <= 476 <= m["end_s"])
    return (calls == 0 and m["label"]["kind"] == "fall" and m["label"]["sure"] and "truck" not in m["label"]["name"]
            and "crash" not in m["label"]["name"] and m["category"] == "stunt"), m["label"]


@check
def a_label_the_peak_does_not_show_is_unsure_and_counts_for_nothing():
    """mined as a crash: where the peak's frames show plain driving it is "unsure", with no impact points and a payoff
    under 3; a tick with no line that says it (a truck "parked in an alleyway") is unsure too; so is a moment nobody has
    looked at yet; a fall only the index saw is unsure"""
    lines = {300.0: ("Player's car crashes into a wall", "driving")}
    drive = labelled(made_up(lines=lines, loud={303.0: 2.0}), 301, [look(301, 305, "Player drives down a quiet street")])
    calm = {300.0: ("Player parks in an alleyway", "driving")}
    rec = made_up(lines=calm)
    m = next(x for x in bank.mine_all(rec)[0] if x["family"] == "calm" and x["start_s"] <= 303 <= x["end_s"])
    m["peak_s"] = 303.0
    bank.set_label(m, rec, [look(302, 306, "Red truck parked in alleyway with characters nearby", crash=True)])
    nobody = labelled(made_up(lines=lines, loud={303.0: 2.0}), 301, [])
    fall = {300.0: ("A man falls in the street", "on_foot")}
    only = labelled(made_up(lines=fall, loud={303.0: 2.0}), 301, [look(301, 305, "Player walks along a street")])
    return (all(x["label"]["kind"] == "unsure" and not x["label"]["sure"] for x in (drive, m, nobody, only))
            and "crash" not in drive["terms"] and all(x["payoff"]["tier"] < 3 for x in (drive, m, nobody, only))
            and "don't show that" in drive["label"]["why"] and "ticks a crash" in m["label"]["why"]
            and "not been looked at" in nobody["label"]["why"] and nobody["label"]["confidence"] == 0.0)


@check
def when_looks_disagree_the_ones_with_most_frames_count():
    """the real bush scrape (_023422, 7:19): one look of 2 frames ticks a crash, four looks with 9 frames don't: unsure; the
    crash into a tree (_164843, 24:12): 3 of 5 frames: a crash; a moment whose peak shows a crash is filed under it though
    its stars go up too (its category, so its type and what it repeats)"""
    lines = {300.0: ("Car drives, then crashes into bushes, causing smoke and debris.", "driving")}
    rec = made_up(lines=lines, loud={301.5: 2.336})
    no = "Black car drives on scenic road with trees and grass."
    scrape = labelled(rec, 301, [look(300.0, 304.0, no), look(301.4, 305.4, no), look(302.0, 306.0, no), look(298.0, 302.0, no),
                                 look(301.0, 302.0, "Car drives down street", every=0.5, crash=True)])
    tree = labelled(made_up(lines=lines, loud={301.5: 3.0}), 301, [look(300.5, 304.5, "street scenes", crash=True),
                                                                   look(299.0, 303.0, "Car drives through streets")])
    counts = [0] * 596 + [1] * 100 + [0] * 504
    both = made_up(lines=lines, loud={301.5: 2.5}, counts=counts, segs=[seg(0, 600, "driving")])
    m = labelled(both, 301, [look(300.5, 304.5, "A car crashes", crash=True)])
    return (scrape["label"]["kind"] == "unsure" and tree["label"]["kind"] == "crash" and "star rise" in m["kinds"]
            and m["terms"].get("star_rise") and m["category"] == "crash" and bank.impact_of(m) == "crash"
            and bank.impact_of(scrape) is None), (scrape["label"], tree["label"], m["category"])


@check
def a_crash_must_be_heard_and_a_wreck_is_not_one():
    """wrong crashes of the first real build (looked at by hand): a truck that turns round (tick and line, but loudness
    1.8), a drift (1.1) and burnt cars after a fight ("wreckage", 0.1) are unsure and no impact; the same tick and
    line with a real crash's sound (2.0) is a crash; where no sound was measured it is unsure too (PR 35: it was a crash)"""
    look_ = [look(302.0, 306.0, "Red Mustang drives and crashes on highway.", crash=True)]
    lines = {300.0: ("Red sports car stuck on road after crash with barrier", "driving")}
    turn = labelled(made_up(lines=lines, loud={303.0: 1.812}), 301, look_)
    drift = labelled(made_up(lines=lines, loud={303.0: 1.143}), 301, look_)
    heard = labelled(made_up(lines=lines, loud={303.0: 2.0}), 301, look_)
    unmeasured = labelled(made_up(lines=lines), 301, [look(302.0, 306.0, "A car crashes on highway.", crash=True)])
    wreck = made_up(lines={300.0: ("Player exits police SUV near wrecked cars and bodies.", "on_foot")}, loud={303.0: 2.5})
    m = next(x for x in bank.mine_all(wreck)[0] if x["start_s"] <= 303 <= x["end_s"])
    m["peak_s"] = 303.0
    bank.set_label(m, wreck, [look(302.0, 306.0, "Player exits vehicle near wreckage in rural setting.", crash=True)])
    return (all(x["label"]["kind"] == "unsure" and x["payoff"]["tier"] < 3 and "crash" not in x["terms"] for x in (turn, drift, m))
            and "nothing that loud" in turn["label"]["why"] and heard["label"]["kind"] == "crash" and heard["payoff"]["tier"] == 3
            and unmeasured["label"]["kind"] == "unsure" and bank.kinds_in("Diver searches wreck, finds treasure") == {"discovery"})


@check
def a_crash_heard_at_the_peak_and_seen_a_second_later_stays_a_crash():
    """the Nov 20 motorbike crash: its sound peaks at 460.5 s; the frames up to 460 show riding, the look from 460.5 s
    shows the crash: "crash", sure (a look that ends before the peak doesn't decide); HUD changes at the peak are sure by
    the HUD, whatever the frames say"""
    lines = {456.0: ("Motorcyclist rides through city streets, crashes into car.", "driving")}
    looks = [look(456.5, 460.5, "Player rides motorcycle through sunny urban streets."),
             look(459.0, 461.0, "Player rides motorcycle through sunny urban streets.", every=0.5),
             look(460.5, 464.5, "Player crashes motorcycle, lies on street, police nearby.", crash=True, police=True)]
    m = labelled(made_up(lines=lines, loud={460.0: 1.978, 460.5: 2.035, 461.0: 1.075}), 458, looks)
    counts = [0] * 400 + [2] * 100 + [0] * 700
    hud = labelled(made_up(segs=[seg(0, 600, "on_foot")], counts=counts), 200, [look(199, 203, "Player walks on a beach")])
    return (m["label"]["kind"] == "crash" and m["label"]["sure"] and m["label"]["confidence"] == 0.95
            and m["payoff"]["tier"] == 3 and "crashes motorcycle" in m["label"]["text"]
            and hud["label"]["kind"] == "star rise" and hud["label"]["confidence"] == 1.0
            and "0 to 2 stars" in hud["label"]["name"])


@check
def a_saved_look_is_used_and_at_most_one_new_one_is_asked():
    """a peak with saved frames round it asks nothing; one without is asked about once, 4 frames from a second before it;
    saved frames that all end before the peak don't count as a look; without the model nothing is asked"""
    m = one(made_up(lines={300.0: ("Player's car crashes into a wall", "driving")}, loud={303.0: 2.0}), 301)
    ai = StandIn()
    saved, c0 = bank.look_at(m, [look(301, 305, "x")], ai, None)
    fresh, c1 = bank.look_at(m, [], ai, None)
    before, c2 = bank.look_at(m, [look(298, 302, "x")], ai, None)
    none, c3 = bank.look_at(m, [], None, None)
    return ((c0, c1, c2, c3) == (0, 1, 1, 0) and len(saved) == len(fresh) == 1 and none == []
            and fresh[0]["from_s"] == 302.0 and fresh[0]["to_s"] == 306.0 and ai.calls == 2)


# --- 4d. Shelves and questions ---

@check
def each_moment_is_on_one_shelf():
    """a crash its peak shows: action; stars that go up and all go, matching the wanted-system topic and the "lose the
    stars" experiment: proof, with the questions it answers (experiments first); the same stars without a question:
    action; a find: a list item; a calm stretch: a list item; a question whose event isn't in the moment is not answered"""
    counts = [0] * 200 + [2] * 40 + [0] * 960
    segs = [seg(0, 100, "driving"), seg(100, 120, "chase", stars=2), seg(120, 600, "driving")]
    lines = {300.0: ("Player's car crashes into a wall", "driving"), 408.0: ("Player swims near a shark", "driving"),
             }
    rec = made_up(segs=segs, counts=counts, lines=lines, loud={303.0: 2.0})
    shotlist = {"topics": [{"id": "wanted-system", "title": "Wanted system", "status": library.SHOT_STATUS,
                            "shots": [{"id": "s", "want": "x", "query": {"activity": ["chase"], "min_stars": 1}}]},
                           {"id": "driving-vehicles", "title": "Driving", "status": library.SHOT_STATUS,
                            "shots": [{"id": "d", "want": "x", "query": {"activity": ["driving"]}}]}],
                "experiments": [{"id": "f1-losing-the-stars", "format": "F1", "try": "Lose a wanted level", "proof": "x",
                                 "query": {"activity": ["chase", "driving"], "min_len": 6}}]}
    with tempfile.TemporaryDirectory() as tmp:
        ai = StandIn("Player's car crashes into a wall", only=[(300, 310)], crash=True)
        ms, _, _, _ = bank.make_moments({rec["stem"]: rec}, ai, tmp, tmp, shotlist, quiet)
        bare, _, _, _ = bank.make_moments({rec["stem"]: rec}, ai, tmp, tmp, {"topics": []}, quiet)
    crash = next(m for m in ms if m["main"] == "crash")
    stars = next(m for m in ms if m["main"] == "star rise")
    find = next(m for m in ms if m["main"] == "discovery")
    calm = [m for m in ms if m["family"] == "calm" and m["label"]["kind"] != "crash"]
    stars_bare = next(m for m in bare if m["main"] == "star rise")
    return (all(m["shelf"] in bank.SHELVES for m in ms) and crash["shelf"] == "action" and stars["shelf"] == "proof"
            and [q["id"] for q in stars["questions"]] == ["f1-losing-the-stars", "wanted-system"]
            and stars_bare["shelf"] == "action" and find["shelf"] == "list" and all(m["shelf"] == "list" for m in calm)
            and not any(q["id"] == "driving-vehicles" for m in ms for q in m["questions"])
            and stars["end_s"] - stars["start_s"] >= 10.0), (stars["questions"], stars["shelf"])


@check
def a_question_needs_what_it_asks_about_on_screen():
    """stars that all go after a chase answer "lose the stars"; they answer "does a new car lose them?" only where the
    index saw a car change in the 45 s before, and "does a new outfit?" only where it saw clothes; a star rise answers the
    robbery topic only in a shop, and the night drive only at night or in the rain (not next to train tracks)"""
    counts = [0] * 200 + [2] * 40 + [0] * 960
    segs = [seg(0, 100, "driving"), seg(100, 120, "chase", stars=2), seg(120, 600, "driving")]
    row = [("GTA_20261119120000", 60.0, 200.0)]
    qs = [{"id": i, "kind": "experiment", "question": "x", "rows": row}
          for i in ("f1-losing-the-stars", "f2-lose-stars-car-swap", "f2-lose-stars-outfit", "heist-robbery",
                    "f1-night-rain-drive")]

    def ids(lines):
        rec = made_up(segs=segs, counts=counts, lines=lines)
        m = one(rec, 120)
        m["label"] = {"kind": "stars lost"}
        return {q["id"] for q in bank.answers_question(m, qs, rec)}
    return (ids({}) == {"f1-losing-the-stars"}
            and ids({90.0: ("Player exits the car and enters a van", "driving")}) == {"f1-losing-the-stars", "f2-lose-stars-car-swap"}
            and ids({90.0: ("Player browses outfits", "on_foot")}) == {"f1-losing-the-stars", "f2-lose-stars-outfit"}
            and ids({90.0: ("Clerk stands behind the counter of a store", "on_foot")}) == {"f1-losing-the-stars", "heist-robbery"}
            and ids({30.0: ("Player exits the car and enters a van", "driving")}) == {"f1-losing-the-stars"}
            and ids({120.0: ("Player drives SUV near train tracks at sunset", "driving")}) == {"f1-losing-the-stars"}
            and ids({120.0: ("Player drives in the rain at night", "driving")}) == {"f1-losing-the-stars", "f1-night-rain-drive"})


@check
def a_moment_that_may_not_be_shown_never_reaches_a_shelf():
    """a phone or a menu at the peak, and a child near shooting (the owner's rule), leave the moment out with the reason,
    in "never_used"; nothing is silently lost"""
    lines = {300.0: ("Player's car crashes into a wall", "driving"), 420.0: ("Player aims gun near a child", "on_foot"),
             426.0: ("A man falls in the street", "on_foot")}
    rec = made_up(lines=lines, loud={303.0: 2.0, 428.0: 2.0})

    class Phone(StandIn):
        def look(self, path, a, b, every, work):
            out = super().look(path, a, b, every, work)
            if a < 310:
                out[0]["answer"]["phone_screen"] = True
            return out
    with tempfile.TemporaryDirectory() as tmp:
        ms, dropped, _, _ = bank.make_moments({rec["stem"]: rec}, Phone(), tmp, tmp, {"topics": []}, quiet)
    why = " | ".join(w for d in dropped for w in d["why"])
    found = bank.mine_all(rec, bank.first_looks({rec["stem"]: rec}))[0]
    return (len(ms) + len(dropped) == len(found) and "phone on screen" in why and "a child is seen" in why
            and not any(m["main"] in ("crash", "fall") for m in ms))


# --- 4a. Raw data, and no second look ---

def clip_run(shorts, stem):
    """What clip.py leaves for a recording: a run folder whose sound has a loud half second every 100 s (3.5, 103.5 ...)."""
    run = Path(shorts) / "launch-2026-11-19" / "clip_runs" / stem / "20261119-120000"
    run.mkdir(parents=True)
    rows = ["time_s,audio_z,visual_z"] + [f"{i * 0.5},{2.0 if (i * 0.5) % 100 == 3.5 else 0.0},0.0" for i in range(1200)]
    (run / "timeline.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return run


def bank_of(tmp, recs_lines, used=None, counts=None):
    """A library, shorts folder and bank folder in tmp with these recordings ({stem: lines})."""
    tmp = Path(tmp)
    lib, shorts = tmp / "library", tmp / "shorts"
    (lib / "_cache").mkdir(parents=True)
    shorts.mkdir()
    videos = {}
    for stem, lines in recs_lines.items():
        video = tmp / f"{stem}.webm"
        video.write_bytes(b"x")
        rec = made_up(stem, lines=lines, counts=counts)
        idx = rec["idx"]
        idx["video"]["path"], idx["video"]["name"] = str(video), video.name
        idx["indexed"] = "2026-11-24T10:00:00"
        (lib / stem).mkdir()
        (lib / stem / "index.json").write_text(json.dumps(idx), encoding="utf-8")
        videos[stem] = {"index": f"{stem}/index.json", "duration_s": 600.0}
        clip_run(shorts, stem)
    (lib / "library.json").write_text(json.dumps({"videos": videos}), encoding="utf-8")
    if used is not None:
        (shorts / footage_short.USED_FOOTAGE).write_text(json.dumps(used), encoding="utf-8")
    return lib, shorts, tmp / "bank"


CRASHES = [(102, 108), (204, 212), (300, 308), (402, 412)]     # where the stand-in model sees one


def crash_at(*seconds):
    return {float(t): ("Player's car crashes into a wall", "driving") for t in seconds}


def build(lib, shorts, bdir, ai):
    return bank.build(lib, shorts, bdir, ai, {"topics": []}, hud6=lambda *a, **k: [], log=quiet)


@check
def raw_data_is_kept_and_mining_again_asks_nothing():
    """the loudness, change and HUD readings of each recording are in bank\\raw; the second build reads them (no HUD is
    read again), finds every peak's answer saved, asks the model nothing and gives the same moments; with the old run
    folders gone, what was measured stays"""
    with tempfile.TemporaryDirectory() as tmp:
        lib, shorts, bdir = bank_of(tmp, {"GTA_20261119120000": crash_at(300)}, counts=[0] * 100 + [2] * 60 + [0] * 1040)
        ai, read = StandIn("Player's car crashes into a wall", cache_dir=lib / "_cache", crash=True), []

        def hud6(path, a, b, prof, **k):
            read.append((a, b))
            return [(a + 0.5 * k, 2, "red" if k < 20 else "red outline", 1) for k in range(40)]
        first = bank.build(lib, shorts, bdir, ai, {"topics": []}, hud6=hud6, log=quiet)
        asked = ai.calls
        raw = json.loads((bdir / "raw" / "GTA_20261119120000.json").read_text(encoding="utf-8"))

        def again(*a, **k):
            raise AssertionError("the HUD was read again")
        second = bank.build(lib, shorts, bdir, ai, {"topics": []}, hud6=again, log=quiet)
        same = [(m["id"], m["label"]["kind"]) for m in first["moments"]] == [(m["id"], m["label"]["kind"]) for m in second["moments"]]
        shutil.rmtree(shorts / "launch-2026-11-19")
        third = bank.build(lib, shorts, bdir, ai, {"topics": []}, hud6=again, log=quiet)
        same = same and [(m["id"], m["peak_s"]) for m in first["moments"]] == [(m["id"], m["peak_s"]) for m in third["moments"]]
    return (asked == first["vision_calls"] > 0 and ai.calls == asked and second["vision_calls"] == 0 and same
            and len(raw["loud"]) == len(raw["act"]) == 1200 and max(raw["loud"]) == 2.0 and read == [(50.0, 80.0)]
            and len(raw["hud6"]["series"]) == 40 and any("star look" in m["kinds"] for m in second["moments"]))


@check
def only_recordings_without_an_index_are_indexed_prepares_way():
    """index_new: a GTA 6 recording already in the library, a GTA 5 one, a 10 s one and held-out footage are left alone;
    a new one is indexed with the profile, its HUD checked as prepare checks it (one that disagrees is indexed again
    without HUD), clip.py runs alongside; a second call does nothing"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        inp, lib, held = tmp / "input", tmp / "library", tmp / "input" / "heldout"
        for d in (inp, lib, held):
            d.mkdir(exist_ok=True)
        (lib / "library.json").write_text(json.dumps({"videos": {"Grand Theft Auto VI_20261119010000": {}}}), encoding="utf-8")
        for name in ("Grand Theft Auto VI_20261119010000", "Grand Theft Auto VI_20261121020000", "Grand Theft Auto V_20261026160754",
                     "Grand Theft Auto VI_20261121030000", "Grand Theft Auto VI_20261121040000"):
            (inp / f"{name}.webm").write_bytes(b"x")
        asked, clips = [], []

        def index_video(video, profile, every, library, cache_dir, log):
            asked.append((Path(video).stem[-6:], profile))
            rec = lt.made_up_idx(Path(video).stem, video)
            if Path(video).stem.endswith("040000") and not profile.endswith("_nohud"):
                blind = made_up_timeline(lt.made_up_counts(), no_minimap=((0, 1200),))
                blind["on"] = True
                rec["_timeline"] = blind
            rec["timing_s"] = {"model_calls": 100}
            data = json.loads((lib / "library.json").read_text(encoding="utf-8"))
            data["videos"][Path(video).stem] = {}
            (lib / "library.json").write_text(json.dumps(data), encoding="utf-8")
            return rec
        old, launch.probe_duration = launch.probe_duration, lambda v: 10.0 if "030000" in str(v) else 600.0
        choose, launch.choose_profile = launch.choose_profile, lambda *a, **k: {"profile": "gta6", "mode": "hud", "why": "x"}
        try:
            kw = {"index_video": index_video, "clip_runner": lambda v, p: clips.append(v.stem[-6:]), "check": False, "log": quiet}
            rows = bank.index_new(inp, lib, tmp / "bank", **kw)
            again = bank.index_new(inp, lib, tmp / "bank", **kw)
        finally:
            launch.probe_duration, launch.choose_profile = old, choose
    return (asked == [("020000", "gta6"), ("040000", "gta6"), ("040000", "gta6_nohud")] and clips == ["020000", "040000"]
            and [r["mode"] for r in rows] == ["hud", "nohud"] and [r["vision_calls"] for r in rows] == [100, 100] and again == [])


# --- 4e. Statuses: footage is locked when it is posted, not when it is rendered ---

def moment(a, b, video="rec_a"):
    return {"video": video, "start_s": float(a), "end_s": float(b)}


@check
def a_rendered_short_locks_nothing_and_a_posted_one_does():
    """save_used(status "rendered"): its footage is free for the next run but not for the other Shorts of the same run; an
    entry with no status (from before) and a posted one stay locked; rendering a posted Short again keeps it posted with
    all it showed; a status from before is never lowered"""
    with tempfile.TemporaryDirectory() as tmp:
        footage_short.save_used(tmp, "old", [["rec_a", 0.0, 10.0]])
        footage_short.save_used(tmp, "1-a@launch-x", [["rec_a", 20.0, 30.0]], "rendered")
        footage_short.set_status(tmp, "2-b@launch-y", [["rec_a", 40.0, 50.0]], "posted", posted="2026-11-24")
        free = launch.all_used_spans(tmp)
        same_run = launch.used_by_others(tmp, "2-z@launch-x", "launch-x")
        other_run = launch.used_by_others(tmp, "2-z@launch-w", "launch-w")
        footage_short.save_used(tmp, "2-b@launch-y", [["rec_a", 41.0, 55.0]], "rendered")
        footage_short.save_used(tmp, "old", [["rec_a", 0.0, 12.0]], "rendered")
        entries = {s["name"]: s for s in footage_short.load_used(tmp)["shorts"]}
        now = launch.all_used_spans(tmp)
    return (free == [("rec_a", 0.0, 10.0), ("rec_a", 40.0, 50.0)] and ("rec_a", 20.0, 30.0) in same_run
            and ("rec_a", 20.0, 30.0) not in other_run and entries["2-b@launch-y"]["status"] == "posted"
            and entries["2-b@launch-y"]["posted"] == "2026-11-24" and ("rec_a", 40.0, 50.0) in now
            and ("rec_a", 41.0, 55.0) in now and "status" not in entries["old"] and ("rec_a", 0.0, 12.0) in now)


@check
@lt.nothing_heard
def a_ready_render_is_rendered_not_posted():
    """launch.render with the stand-in voice: a READY Short's footage is logged with status "rendered" and is not locked,
    so a later run may offer it again"""
    pick = dict(lt.chase_pick(), bank_id="1119-120000-0100")
    with tempfile.TemporaryDirectory() as tmp:
        shorts = Path(tmp) / "shorts"
        shorts.mkdir()
        folder = lt.run_folder_with(tmp, [pick], [lt.good_narrated(pick)])
        data = launch.render(folder, shorts_dir=shorts, today=lt.TODAY, log=quiet, facts=lt.FACTS, checker=None,
                             timing=lt.stand_in_voice, narrated_render=lt.stand_in_explainer, indexes=[lt.made_up_idx()],
                             engine="v1")
        used = footage_short.load_used(shorts)["shorts"]
        locked = launch.all_used_spans(shorts)
        status, name = bank.status_of(moment(110, 130), bank.status_spans(shorts))
    e = data["shorts"][0]
    return (e["status"] == "READY" and e["bank_id"] == "1119-120000-0100" and [s["status"] for s in used] == ["rendered"]
            and used[0]["spans"] and locked == [] and status == "rendered" and name == used[0]["name"])


@check
def a_moments_status_comes_from_the_spans():
    """new; rendered and posted: half a second or more of it was shown (posted wins); rejected: half of it lies in a
    rejected moment, so its neighbours stay free; an entry with no status counts as posted"""
    spans = {"posted": [("rec_a", 100.0, 110.0, "p")], "rendered": [("rec_a", 105.0, 140.0, "r")],
             "rejected": [("rec_a", 200.0, 212.0, "x")]}
    s = lambda a, b, v="rec_a": bank.status_of(moment(a, b, v), spans)[0]
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / footage_short.USED_FOOTAGE).write_text(json.dumps({"shorts": [
            {"name": "before", "spans": [["rec_a", 1.0, 2.0]]}, {"name": "r", "spans": [["rec_a", 5.0, 6.0]], "status": "rendered"}],
            "rejected": [{"id": "x", "spans": [["rec_a", 9.0, 10.0]]}]}), encoding="utf-8")
        read = bank.status_spans(tmp)
    return ([s(0, 20), s(95, 108), s(112, 130), s(199, 211), s(208, 230), s(95, 100.2), s(100, 110, "rec_b")]
            == ["new", "posted", "rendered", "rejected", "new", "new", "new"]
            and [len(read[k]) for k in ("posted", "rendered", "rejected")] == [1, 1, 1] and read["posted"][0][3] == "before")


@check
def marking_a_moment_posted_locks_it_and_adds_its_topic():
    """bank.py posted ID: the moment's seconds go into _used_footage.json as posted, its topic into posted_topics.json
    (type, day, rules) with every topic that was there unchanged to the byte, both files are copied first, the bank's
    status changes; the same name twice, a day that isn't one, an unknown id and a moment with no name are refused and
    change nothing. A finished Short's folder works too: its render's own spans"""
    fixture = (HERE / "testdata" / "posted_topics_2026-11-23.json").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        lib, shorts, bdir = bank_of(tmp, {"GTA_20261119120000": crash_at(300)}, used={"shorts": []})
        posted = Path(tmp) / "posted_topics.json"
        posted.write_text(fixture, encoding="utf-8", newline="")
        b = build(lib, shorts, bdir, StandIn("Player's car crashes into a wall", crash=True))
        m = next(x for x in b["moments"] if x["main"] == "crash")
        kw = {"shorts_dir": shorts, "bank_dir": bdir, "posted_file": posted, "log": quiet}
        topic, spans = bank.mark_posted(m["id"], "car into a wall", on="2026-11-24", url="https://example.com/x", **kw)
        text = posted.read_text(encoding="utf-8")
        head = fixture.rstrip().rsplit("]", 1)[0].rstrip()
        used = footage_short.load_used(shorts)["shorts"]
        after = next(x for x in bank.read_bank(bdir)["moments"] if x["id"] == m["id"])
        kept = sorted((bdir / "backups").glob("*/posted_topics.json"))
        same = kept[0].read_text(encoding="utf-8") == fixture
        refused = []
        state = (posted.read_bytes(), (shorts / footage_short.USED_FOOTAGE).read_bytes())
        for args in (((m["id"], "car into a wall"), {}), ((m["id"], "y"), {"on": "soon"}), (("nope", "y"), {}), ((m["id"], None), {})):
            try:
                bank.mark_posted(*args[0], **args[1], **kw)
            except bank.BankError as e:
                refused.append(str(e))
        unchanged = state == (posted.read_bytes(), (shorts / footage_short.USED_FOOTAGE).read_bytes())
        run = shorts / "launch-2026-11-24"
        (run / "1-a-short").mkdir(parents=True)
        (run / "render.json").write_text(json.dumps({"shorts": [{"short": 1, "folder": str(run / "1-a-short"), "title": "A short",
                                                                 "shown": [["GTA_20261119120000", 450.0, 460.0]]}]}), encoding="utf-8")
        footage_short.save_used(shorts, "1-a-short@launch-2026-11-24", [["GTA_20261119120000", 450.0, 460.0]], "rendered")
        bank.mark_posted(str(run / "1-a-short"), "a whole short", kind="crash", words_=["whole"], on="2026-11-24", **kw)
        short = next(s for s in footage_short.load_used(shorts)["shorts"] if s["name"] == "1-a-short@launch-2026-11-24")
        topics = launch.load_posted(posted, log=quiet)
    return (text.startswith(head) and topic["posted"] == "2026-11-24" and topic["type"] == "crash"
            and topic["rules"][0]["term"] == ["crash"] and spans == [["GTA_20261119120000", m["start_s"], m["end_s"]]]
            and used[0]["status"] == "posted" and "example.com" in used[0]["note"] and after["status"] == "posted" and same
            and len(refused) == 4 and unchanged and short["status"] == "posted" and len(short["spans"]) == 1
            and len(topics) == 17 and topics[-1]["name"] == "a whole short"), refused


@check
def marking_by_id_locks_all_the_short_showed():
    """/launch made a Short of the moment that showed more than it (its window starts 15 s earlier): marking the moment's id
    posted turns that Short's own entry to posted, all it showed is locked and no second entry is made; a dry run's render
    of it doesn't count; a moment no Short was made of locks its own seconds"""
    with tempfile.TemporaryDirectory() as tmp:
        lib, shorts, bdir = bank_of(tmp, {"GTA_20261119120000": crash_at(102, 300)}, used={"shorts": []})
        posted = Path(tmp) / "posted_topics.json"
        shutil.copyfile(launch.POSTED_FILE, posted)
        b = build(lib, shorts, bdir, StandIn("Player's car crashes into a wall", only=CRASHES, crash=True))
        first, second = sorted((x for x in b["moments"] if x["main"] == "crash"), key=lambda x: x["start_s"])
        for run, dry in (("launch-2026-11-24", False), ("trial", True)):
            (shorts / run / "1-a-short").mkdir(parents=True)
            (shorts / run / "render.json").write_text(json.dumps({"dry_run": dry, "shorts": [
                {"short": 1, "status": "READY", "bank_id": second["id"], "folder": str(shorts / run / "1-a-short"),
                 "title": "Car into a wall", "shown": [["GTA_20261119120000", 280.0, 296.0], ["GTA_20261119120000", 296.0, 312.0]]}]}),
                encoding="utf-8")
        footage_short.save_used(shorts, "1-a-short@launch-2026-11-24", [["GTA_20261119120000", 280.0, 296.0]], "rendered")
        kw = {"shorts_dir": shorts, "bank_dir": bdir, "posted_file": posted, "log": quiet, "on": "2026-11-24"}
        bank.mark_posted(second["id"], "car into a wall", **kw)
        one_ = footage_short.load_used(shorts)["shorts"]
        locked = launch.all_used_spans(shorts)
        bank.mark_posted(first["id"], "another wall", **kw)
        two = footage_short.load_used(shorts)["shorts"]
    return (second["start_s"] > 285 and [s["name"] for s in one_] == ["1-a-short@launch-2026-11-24"]
            and one_[0]["status"] == "posted" and ("GTA_20261119120000", 280.0, 296.0) in locked
            and ("GTA_20261119120000", 296.0, 312.0) in locked and [s["name"] for s in two][1] == f"{first['id']}@bank"
            and two[1]["spans"] == [["GTA_20261119120000", first["start_s"], first["end_s"]]]), (one_, two)


@check
def a_posted_shorts_type_is_never_guessed():
    """a Short from before the bank, marked by its folder: its type comes from its pick in that run's candidates.json (the
    six wanted stars: star rise); with no pick there it is refused, nothing changes, and --type says it"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        shorts, bdir, posted = tmp / "shorts", tmp / "bank", tmp / "posted_topics.json"
        shutil.copyfile(launch.POSTED_FILE, posted)
        run = shorts / "launch-2026-11-19-part3"
        (run / "2-six-stars").mkdir(parents=True)
        e = {"short": 2, "moment": "M4", "status": "READY", "folder": str(run / "2-six-stars"), "title": "GTA 6 has SIX wanted stars",
             "shown": [["rec_a", 60.0, 62.0]]}
        (run / "render.json").write_text(json.dumps({"shorts": [e]}), encoding="utf-8")
        kw = {"shorts_dir": shorts, "bank_dir": bdir, "posted_file": posted, "log": quiet, "on": "2026-11-24"}
        before = posted.read_bytes()
        refused = None
        try:
            bank.mark_posted(str(run / "2-six-stars"), **kw)
        except bank.BankError as err:
            refused = str(err)
        untouched = posted.read_bytes() == before and not (shorts / footage_short.USED_FOOTAGE).exists()
        (run / "candidates.json").write_text(json.dumps({"picks": [{"short": 2, "id": "M4", "category": "star rise",
                                                                    "terms": {"star_rise": 2.5}}]}), encoding="utf-8")
        topic, _ = bank.mark_posted(str(run / "2-six-stars"), **kw)
        said, _ = bank.mark_posted(str(run / "2-six-stars"), name="the same again", kind="discovery", **kw)
    return refused and "--type" in refused and untouched and topic["type"] == "star rise" and said["type"] == "discovery"


@check
def marking_a_moment_rejected_takes_only_that_moment_out():
    """bank.py rejected ID: the moment is never offered again and its footage counts as locked; the Shorts of
    _used_footage.json stay as they are, the other moments stay new, the file is copied first"""
    with tempfile.TemporaryDirectory() as tmp:
        lib, shorts, bdir = bank_of(tmp, {"GTA_20261119120000": crash_at(102, 300)},
                                    used={"shorts": [{"name": "s", "spans": [["z", 1.0, 2.0]], "status": "posted"}]})
        b = build(lib, shorts, bdir, StandIn("Player's car crashes into a wall", only=CRASHES, crash=True))
        ms = [x for x in b["moments"] if x["main"] == "crash"]
        bank.mark_rejected(ms[0]["id"], "dull", shorts, bdir, quiet)
        used = footage_short.load_used(shorts)
        now = {x["id"]: x["status"] for x in bank.read_bank(bdir)["moments"]}
        recs = bank.load_recs(lib, shorts, bdir, hud6=lambda *a, **k: [], log=quiet)
        offered = {p["bank_id"] for p in bank.candidates(b["moments"], recs, bank.status_spans(shorts))}
        copies = list((bdir / "backups").glob("*/_used_footage.json"))
        footage_short.save_used(shorts, "later", [["z", 3.0, 4.0]], "rendered")
        still = footage_short.load_used(shorts)["rejected"]
    return (len(ms) == 2 and now[ms[0]["id"]] == "rejected" and now[ms[1]["id"]] == "new" and ms[0]["id"] not in offered
            and ms[1]["id"] in offered and used["shorts"] == [{"name": "s", "spans": [["z", 1.0, 2.0]], "status": "posted"}]
            and used["rejected"][0]["why"] == "dull" and ("GTA_20261119120000", ms[0]["start_s"], ms[0]["end_s"]) in launch_spans(used)
            and copies and len(still) == 1)


def launch_spans(used):
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / footage_short.USED_FOOTAGE).write_text(json.dumps(used), encoding="utf-8")
        return launch.all_used_spans(tmp)


@check
def the_migration_loses_no_entry():
    """the owner's real _used_footage.json of Nov 23 (a copy): all 11 entries stay, with every span, day and note; the 8
    posted ones are posted (3 said so; 5 are posted topics with a posted day), the 3 rendered and never posted (six stars,
    the bridge jam, the witness) are free; the files are copied first; a second run changes nothing; a file it would
    damage is not written"""
    src = HERE / "testdata" / "used_footage_2026-11-23.json"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        shutil.copyfile(src, tmp / footage_short.USED_FOOTAGE)
        before, after, saved = bank.migrate(tmp, tmp / "bank", HERE / "testdata" / "posted_topics_2026-11-23.json", quiet)
        file_ = json.loads((tmp / footage_short.USED_FOOTAGE).read_text(encoding="utf-8"))
        copied = (saved / footage_short.USED_FOOTAGE).read_bytes() == src.read_bytes()
        free = {s["name"].split("@")[0][:22] for s in file_["shorts"] if s["status"] == "rendered"}
        first = (tmp / footage_short.USED_FOOTAGE).read_bytes()
        bank.migrate(tmp, tmp / "bank", HERE / "testdata" / "posted_topics_2026-11-23.json", quiet)
        twice = (tmp / footage_short.USED_FOOTAGE).read_bytes() == first
        locked = len(launch.all_used_spans(tmp))
    old = json.loads(src.read_text(encoding="utf-8"))
    kept = all(a["name"] == b["name"] and a["spans"] == b["spans"] and (b.get("posted") in (None, a.get("posted")))
               and b.get("note") == a.get("note") for a, b in zip(file_["shorts"], old["shorts"]))
    unlocked = sum(len(s["spans"]) for s in file_["shorts"] if s["status"] == "rendered")
    return (len(old["shorts"]) == len(file_["shorts"]) == 11 and kept and copied and twice and before == old
            and sum(s["status"] == "posted" for s in file_["shorts"]) == 8
            and free == {"2-gta-6-has-six-wanted", "4-gta-6-bridge-jam-jus", "3-why-gta-6-cops-neede"}
            and all(s.get("posted") for s in file_["shorts"] if s["status"] == "posted")
            and locked == sum(len(s["spans"]) for s in old["shorts"]) - unlocked), (free, locked)


# --- 4g. The picker ---

@check
def the_picker_is_never_offered_posted_or_rejected_footage():
    """bank.candidates and pick_step: new and rendered moments only; the posted and the rejected crash are in no pool and
    no pick, the list items and calm stretches aren't offered for a Short; a rendered one ranks after a new one of the
    same payoff even with more points; the payoff bar is still 2/3"""
    with tempfile.TemporaryDirectory() as tmp:
        every = crash_at(102, 204, 300, 402)
        lib, shorts, bdir = bank_of(tmp, {"GTA_20261119120000": every, "GTA_20261119130000": every})
        ai = StandIn("Player's car crashes into a wall", only=CRASHES, crash=True)
        b = build(lib, shorts, bdir, ai)
        ms = sorted((m for m in b["moments"] if m["main"] == "crash"), key=lambda m: (m["video"], m["start_s"]))
        posted, rejected, rendered, *new = ms
        (shorts / footage_short.USED_FOOTAGE).write_text(json.dumps({"shorts": [
            {"name": "p", "spans": [[posted["video"], posted["start_s"], posted["end_s"]]], "status": "posted"},
            {"name": "r", "spans": [[rendered["video"], rendered["start_s"], rendered["end_s"]]], "status": "rendered"}],
            "rejected": [{"id": rejected["id"], "spans": [[rejected["video"], rejected["start_s"], rejected["end_s"]]]}]}), encoding="utf-8")
        recs = bank.load_recs(lib, shorts, bdir, hud6=lambda *a, **k: [], log=quiet)
        cands = bank.candidates(b["moments"], recs, bank.status_spans(shorts))
        offered = {c["bank_id"] for c in cands}

        def safe(m, rec, folder):
            m["verified"] = {"crash": [[m["peak_s"] - 1.0, m["peak_s"] + 1.0]]}
            return True, []
        moments, picks, _, pool = launch.pick_step(recs, [Path(r["path"]) for r in recs.values()], (), [], safe, tmp, "gta6", 3,
                                                   False, True, quiet, lambda *a, **k: [], [], cands)
        a = {"id": "new", "video": "v1", "start_s": 0.0, "end_s": 20.0, "session_s": 1.0, "score": 2.0, "category": "crash",
             "terms": {"crash": 1.5}, "status": "new"}
        old = dict(a, id="rendered", video="v2", score=9.0, status="rendered")
        first = launch.pick_varied([dict(old), dict(a)], 1, log=quiet)[0]["id"]
        weak = launch.pick_varied([dict(a, terms={"activity": 2.0})], 1, log=quiet)
        rescored = next(c for c in cands if c["bank_id"] == rendered["id"])
    ids = {m["bank_id"] for m in pool} | {m["bank_id"] for m in picks}
    return (len(ms) == 8 and offered == {rendered["id"], *(m["id"] for m in new)} and ids <= offered and picks
            and all(c["shelf"] != "list" for c in cands) and first == "new" and weak == []
            and rescored["terms"]["rendered"] == -bank.RENDERED_COST and len(moments) == 6), (offered, ids)


@check
def two_candidates_of_a_night_never_show_the_same_seconds():
    """moments of different kinds may overlap in the bank (a find inside a shootout), but the picker is offered only the
    best of the ones that share more than 2 s; a small kind (a reaction, a find, a mechanic) is a list item whatever its
    peak shows, never an action clip"""
    lines = {204.0: ("Player aims gun at a shark", "shootout")}
    segs = [seg(0, 200, "driving"), seg(200, 215, "shootout"), seg(215, 600, "driving")]
    rec = made_up(lines=lines, segs=segs, loud={206.0: 2.0})
    recs = {rec["stem"]: rec}
    with tempfile.TemporaryDirectory() as tmp:
        ms, _, _, _ = bank.make_moments(recs, StandIn("Player aims gun at a shark", only=[(200, 215)], shooting=True), tmp, tmp,
                                        {"topics": []}, quiet)
    find = next(m for m in ms if m["main"] == "discovery")
    shoot = next(m for m in ms if m["main"] == "shooting")
    twice = [dict(shoot, id="a", status="new"), dict(shoot, id="b", status="new", score=shoot["score"] - 1)]
    offered = [c["bank_id"] for c in bank.candidates(ms, recs)]
    return (find["shelf"] == "list" and find["label"]["kind"] == "shooting" and shoot["shelf"] == "action"
            and launch.overlap(find["start_s"], find["end_s"], shoot["start_s"], shoot["end_s"]) > 2.0
            and offered == [shoot["id"]] and [c["bank_id"] for c in bank.candidates(twice, recs)] == ["a"])


@check
def a_short_may_not_grow_into_locked_footage():
    """a moment next to posted footage: the stretch its Short may use ends where the posted footage begins (and starts
    where it ends), so its window never shows a posted second; a moment far from it keeps its whole stretch"""
    with tempfile.TemporaryDirectory() as tmp:
        lib, shorts, bdir = bank_of(tmp, {"GTA_20261119120000": crash_at(300)}, used={"shorts": [
            {"name": "p", "spans": [["GTA_20261119120000", 260.0, 290.0], ["GTA_20261119120000", 330.0, 340.0]], "status": "posted"}]})
        b = build(lib, shorts, bdir, StandIn("Player's car crashes into a wall", crash=True))
        recs = bank.load_recs(lib, shorts, bdir, hud6=lambda *a, **k: [], log=quiet)
        c = next(c for c in bank.candidates(b["moments"], recs, bank.status_spans(shorts)) if c["kinds"] == ["crash"])
        w = launch.type_options(recs[c["video"]], c)
        free = next(c for c in bank.candidates(b["moments"], recs, {"posted": [], "rendered": [], "rejected": []}) if c["kinds"] == ["crash"])
    windows = [o["window"] for k, o in w.items() if isinstance(o, dict)]
    return (c["span"] == [290.0, 330.0] and windows and all(290.0 <= a and b <= 330.0 for a, b in windows)
            and free["span"] == [0.0, 600.0]), (c["span"], windows, free["span"])


@check
def an_impact_elsewhere_in_the_footage_does_not_name_the_moment():
    """a candidate after its safety checks: a crash seen 4 s before the peak (M24's pickup) is dropped, the jump at the
    peak counts as a stunt, so its category, its payoff and its evidence say stunt and not crash; a crash at the peak
    stays"""
    rec = m24()
    m = labelled(rec, 476, M24_LOOKS)
    recs = {rec["stem"]: rec}
    c = bank.candidates([dict(m, status="new")], recs)[0]
    c["id"] = "M1"
    c["verified"] = safety.seen_spans(M24_LOOKS)
    bank.keep_what_the_peak_shows(c)
    launch.verify_terms(rec, c)
    good, why = launch.story_gate(rec, c, "gta6", False)
    bike = {"peak_s": 460.5, "label": {"kind": "crash", "sure": True}, "terms": {"crash": 1.5},
            "verified": {"crash": [[460.5, 464.5]], "police": [[460.5, 464.5]]}}
    bank.keep_what_the_peak_shows(bike)
    values = {e["value"] for o in c["point"]["action_payoff"] for e in [{"value": o}]}
    return (good and "crash" not in c["terms"] and c["category"] == "stunt" and launch.moment_type(c) == "stunt"
            and values == {"stunt"} and launch.payoff_strength(c)[0] == 3 and c["verified"].get("crash") is None
            and [460.5, 464.5] in bike["verified"]["crash"] and bike["verified"]["police"]), (c["terms"], c["point"])


@check
def prepare_picks_from_the_bank_and_needs_no_new_recording():
    """launch.prepare with a bank: one new recording is indexed and joins the bank; the picks come from every recording,
    the old one included, none of them posted footage; candidates.json lists the new recording and those of its picks,
    with the bank's ids and labels; a second /launch with nothing new still picks; without a bank it stops as before"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        lib, shorts, bdir = bank_of(tmp, {"GTA_20261119120000": crash_at(102, 300)})
        inp = tmp / "input"
        inp.mkdir()
        new = inp / "Grand Theft Auto VI_20261121010000.webm"
        new.write_bytes(b"x")
        footage_short.set_status(shorts, "p", [["GTA_20261119120000", 95.0, 115.0]], "posted", posted="2026-11-20")

        def index_video(video, profile, every, library, cache_dir, log):
            rec = made_up(Path(video).stem, lines=crash_at(204))
            idx = dict(rec["idx"], indexed="2026-11-24T11:00:00")
            idx["video"] = dict(idx["video"], path=str(video), name=Path(video).name)
            (lib / Path(video).stem).mkdir(exist_ok=True)
            (lib / Path(video).stem / "index.json").write_text(json.dumps(idx), encoding="utf-8")
            data = json.loads((lib / "library.json").read_text(encoding="utf-8"))
            data["videos"][Path(video).stem] = {}
            (lib / "library.json").write_text(json.dumps(data), encoding="utf-8")
            return idx

        class Safe(StandIn):
            def __call__(self, m, rec, folder):
                m["verified"] = {"crash": [[m["peak_s"] - 1.0, m["peak_s"] + 1.0]]}
                return True, []
        keep = launch.probe_duration, launch.choose_profile, launch.POSTED_FILE
        launch.probe_duration = lambda v: 600.0
        launch.choose_profile = lambda *a, **k: {"profile": "gta6", "mode": "hud", "stars": True, "why": "x", "attempts": []}
        launch.POSTED_FILE = tmp / "none.json"
        kw = {"input_dir": inp, "shorts_dir": shorts, "library_dir": lib, "log": quiet, "index_video": index_video,
              "clip_runner": lambda v, p: clip_run(shorts, v.stem), "check": False, "sheets": False, "posted_file": tmp / "none.json",
              "safety": Safe("Player's car crashes into a wall", only=CRASHES, crash=True), "hud6": lambda *a, **k: []}
        try:
            folder = launch.prepare("gta6", day="2026-11-24", bank_dir=bdir, **kw)
            data = json.loads((folder / "candidates.json").read_text(encoding="utf-8"))
            in_bank = {m["video"] for m in bank.read_bank(bdir)["moments"]}
            again = launch.prepare("gta6", day="2026-11-25", bank_dir=bdir, **kw)
            data2 = json.loads((again / "candidates.json").read_text(encoding="utf-8"))
            stopped = None
            try:
                launch.prepare("gta6", day="2026-11-26", **kw)
            except launch.LaunchError as e:
                stopped = str(e)
            # a night on which nothing passes says so and is not carried on by the next one
            (inp / "Grand Theft Auto VI_20261127010000.webm").write_bytes(b"x")
            footage_short.set_status(shorts, "all", [[v, 0.0, 600.0] for v in (
                "GTA_20261119120000", "Grand Theft Auto VI_20261121010000", "Grand Theft Auto VI_20261127010000")], "posted",
                posted="2026-11-24")
            nothing = None
            try:
                launch.prepare("gta6", day="2026-11-27", bank_dir=bdir, **kw)
            except launch.LaunchError as e:
                nothing = str(e)
            (inp / "Grand Theft Auto VI_20261128010000.webm").write_bytes(b"x")
            left, _ = launch.find_recordings(inp, lib, launch.load_runs(shorts))
        finally:
            launch.probe_duration, launch.choose_profile, launch.POSTED_FILE = keep
    picks = data["picks"]
    hit = [p for p in picks if p["video"] == "GTA_20261119120000" and launch.overlap(p["window"][0], p["window"][1], 95.0, 115.0) > 0]
    return (in_bank == {"GTA_20261119120000", "Grand Theft Auto VI_20261121010000"} and {p["video"] for p in picks} == in_bank and not hit
            and all(p["bank_id"] and p["label"]["kind"] == "crash" for p in picks)
            and {r["stem"] for r in data["recordings"]} == in_bank and data2["picks"] and folder != again
            and stopped and "no new recordings" in stopped and nothing and "no moment passed" in nothing
            and [v.name for v in left] == ["Grand Theft Auto VI_20261128010000.webm"]), ([(p["video"], p["window"]) for p in picks], stopped)


@check
def launch_run_as_a_program_shares_its_module_with_the_bank():
    """python launch.py ... --bank: bank.py's "import launch" is the running program, so an error the bank raises stops it
    with a plain ERROR line and exit code 2, not a traceback"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        lib, shorts, bdir = bank_of(tmp, {"GTA_20261119120000": crash_at(300)})
        run = shorts / "launch-2026-11-20"
        run.mkdir()
        (run / "candidates.json").write_text(json.dumps({"game": "gta6", "created": "2026-11-20T15:00:00", "recordings": [
            {"stem": "GTA_20261119120000", "path": "x", "mode": "hud", "clip_run": None, "session_offset_s": 0.0}],
            "settings": {"library": str(lib)}, "picks": []}), encoding="utf-8")
        code = chr(10).join([f"HERE, RUN, LAUNCH = {str(HERE)!r}, {str(run)!r}, {str(HERE / 'launch.py')!r}",
                          'import runpy, sys',
                          "sys.path.insert(0, HERE); sys.argv = ['launch.py', 'repick', '--run', RUN, '--bank']",
                          'import bank, safety',
                          'def broken(*a, **k):',
                          '    import launch',
                          "    raise launch.LaunchError('made up')",
                          'bank.load_recs = broken',
                          'safety.Checker = lambda *a, **k: None',
                          "del sys.modules['launch']",
                          "runpy.run_path(LAUNCH, run_name='__main__')"])
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=HERE,
                              env=dict(os.environ, GTA_HEAVY_LOCK=str(tmp / "lock.json")))
    return done.returncode == 2 and "ERROR: made up" in done.stderr and "Traceback" not in done.stderr, done.stderr[-600:]


@check
def repick_can_use_the_banks_labels():
    """launch.repick(bank_dir): the run's recordings' moments labelled from their peaks, with the footage locked then; the
    run folder stays as it is; without bank_dir it picks as it always did"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        lib, shorts, bdir = bank_of(tmp, {"GTA_20261119120000": crash_at(300)})
        run = shorts / "launch-2026-11-20"
        run.mkdir()
        cand = {"game": "gta6", "dry_run": True, "count": 1, "created": "2026-11-20T15:06:03", "settings": {"library": str(lib)},
                "recordings": [{"stem": "GTA_20261119120000", "path": str(tmp / "GTA_20261119120000.webm"), "mode": "hud",
                                "clip_run": None, "session_offset_s": 0.0}],
                "picks": [{"id": "M1", "video": "GTA_20261119120000", "start_s": 100.0, "end_s": 130.0, "score": 5.0,
                           "category": "crash", "terms": {"crash": 1.5}}]}
        (run / "candidates.json").write_text(json.dumps(cand), encoding="utf-8")
        before = hashlib.sha1((run / "candidates.json").read_bytes()).hexdigest()

        class Safe(StandIn):
            def __call__(self, m, rec, folder):
                m["verified"] = {"crash": [[m["peak_s"] - 1.0, m["peak_s"] + 1.0]]} if m.get("peak_s") else {}
                return True, []
        data = launch.repick(run, shorts_dir=shorts, safety=Safe("Player's car crashes into a wall", crash=True), log=quiet,
                             hud6=lambda *a, **k: [], bank_dir=bdir, out=tmp / "out")
        same = hashlib.sha1((run / "candidates.json").read_bytes()).hexdigest() == before
    p = data["new_picks"][0]
    return same and p["bank_id"] and p["label"]["kind"] == "crash" and p["payoff_strength"]["tier"] == 3


# --- 4f. The bank file and its page ---

@check
def the_bank_file_and_its_page_show_every_shelf():
    """content_bank.json: every moment has its id, recording, start, end, shelf, label, how sure it is, payoff, status; ids
    are unique. bank.html: the three shelves with their totals, minutes covered of all recorded, best first, 3 pictures a
    moment from the index's own frames, the commands to mark it, calm stretches folded away; text is escaped"""
    with tempfile.TemporaryDirectory() as tmp:
        lines = {**crash_at(300), 408.0: ("Player swims near a <b>shark</b>", "driving")}
        lib, shorts, bdir = bank_of(tmp, {"GTA_20261119120000": lines})
        b = build(lib, shorts, bdir, StandIn("Player's car crashes into a wall, sees a <b>shark</b>", only=CRASHES, crash=True))
        saved = json.loads((bdir / bank.BANK_FILE).read_text(encoding="utf-8"))
        page = (bdir / bank.PAGE_FILE).read_text(encoding="utf-8")
    need = ("id", "video", "start_s", "end_s", "shelf", "label", "payoff", "score", "status")
    ids = [m["id"] for m in saved["moments"]]
    t = saved["totals"]
    articles = re.findall(r'<article class="m (\w+)" id="([^"]+)"', page)
    shown = [m["payoff"]["tier"] for m in saved["moments"] if m["shelf"] == "action"]
    return (all(k in m for m in saved["moments"] for k in need) and len(set(ids)) == len(ids) == len(articles)
            and all("sure" in m["label"] and "confidence" in m["label"] for m in saved["moments"])
            and t["recorded_s"] == 600.0 and 500 < t["covered_s"] <= 600 and t["covered_without_calm_s"] < t["covered_s"]
            and sum(t["shelves"].values()) == len(ids) and all(f'id="{k}"' in page for k in bank.SHELVES)
            and page.count('class="pics"') == len(ids) and all(len(m["pictures"]) == 3 for m in saved["moments"])
            and "library/GTA_20261119120000/frames/f_" in page and f"posted {ids[0]}" in page and f"rejected {ids[0]}" in page
            and "<b>shark</b>" not in page and "&lt;b&gt;shark" in page and "<details>" in page
            and shown == sorted(shown, reverse=True) and "of 10" in page)


@check
def the_saved_answers_are_read_from_the_index_cache():
    """saved_answers reads the safety question's own cache files (this prompt's version, whole frames), skips the
    subtitle band, the index's sheets, other prompts and broken files"""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "rec"
        d.mkdir()
        good = {"fields": {"from_ms": 1000, "to_ms": 5000, "every_s": 1.0, "prompt_version": safety.PROMPT_VERSION},
                "record": {"answer": answer("ok")}}
        files = {"a": good, "b": dict(good, fields=dict(good["fields"], band=[0, 0, 1, 1])),
                 "c": {"fields": {"t_ms": 0, "prompt_version": "index_v1"}, "record": {"answer": {"notable": "x"}}},
                 "d": dict(good, fields=dict(good["fields"], prompt_version="safety_v2")),
                 "e": dict(good, record={"answer": None})}
        for k, v in files.items():
            (d / f"{k}.json").write_text(json.dumps(v), encoding="utf-8")
        (d / "f.json").write_text("not json", encoding="utf-8")
        got = bank.saved_answers(tmp, "rec")
        none = bank.saved_answers(tmp, "other")
    return [(s["from_s"], s["to_s"], s["answer"]["note"]) for s in got] == [(1.0, 5.0, "ok")] and none == []


# --- PR 35: the outside review's findings (docs/breaks-pr35.md says which of these failed on main) ---

def stops(act, *words_):
    """Whether act() stops with one of the errors the programs print as a plain ERROR line, naming these words."""
    try:
        act()
    except (bank.BankError, launch.LaunchError, index.IndexError_) as e:
        return all(w in str(e) for w in words_)
    return False


@check
def a_damaged_record_stops_everything_and_keeps_every_byte():
    """B1: _used_footage.json cut short, locked (a folder in its place) or of the wrong shape was read as empty, and the
    next save wrote that, unlocking every posted second. Reading it, adding a Short, marking one and listing what is
    locked now stop with its name and change nothing; posted_topics.json and _launch_runs.json the same; only a file that
    isn't there counts as empty"""
    good = json.dumps({"shorts": [{"name": "p", "status": "posted", "spans": [["rec_a", 1.0, 2.0]]}]})
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        used, topics, runs = tmp / footage_short.USED_FOOTAGE, tmp / "posted_topics.json", tmp / launch.RUNS_LOG
        acts = (lambda: footage_short.load_used(tmp), lambda: footage_short.save_used(tmp, "n", [["rec_a", 5.0, 6.0]], "rendered"),
                lambda: footage_short.set_status(tmp, "n", [["rec_a", 5.0, 6.0]], "posted"), lambda: bank.status_spans(tmp),
                lambda: launch.all_used_spans(tmp))
        empty = footage_short.load_used(tmp) == {"shorts": []} and launch.load_posted(topics, log=quiet) == []
        empty = empty and launch.load_runs(tmp) == {"runs": []} and stops(lambda: bank.add_topic({"name": "n"}, topics), "isn't there")
        for text in (good[:40], "", "[]", '{"shorts": {}}'):
            used.write_text(text, encoding="utf-8")
            out += [stops(act, footage_short.USED_FOOTAGE) for act in acts]
            out.append(used.read_text(encoding="utf-8") == text)
        for text in ('{"topics": [{', "[]", '{"topics": {}}', "{}"):
            topics.write_text(text, encoding="utf-8")
            out.append(stops(lambda: launch.load_posted(topics, log=quiet), "posted_topics.json"))
            out.append(stops(lambda: bank.add_topic({"name": "n", "rules": []}, topics), "posted_topics.json"))
            out.append(topics.read_text(encoding="utf-8") == text)
        runs.write_text('{"runs": [', encoding="utf-8")
        out.append(stops(lambda: launch.note_run(tmp, tmp / "x", [], "prepared"), launch.RUNS_LOG))
        out.append(runs.read_text(encoding="utf-8") == '{"runs": [')
        used.unlink()
        used.mkdir()                                      # what can't be opened at all
        out += [stops(act, footage_short.USED_FOOTAGE) for act in acts]
    return empty and all(out), out


@check
def a_topic_is_checked_before_the_list_is_replaced():
    """B2: add_topic put the new posted_topics.json in place and read it back afterwards, so a topic the picker can't read
    (a rule it doesn't know, a day that isn't one) stopped every later /launch. Now it is refused before anything is
    replaced: every byte stays, no temp file is left, and a good topic is still added"""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "posted_topics.json"
        shutil.copyfile(launch.POSTED_FILE, path)
        before = path.read_bytes()
        bad = [{"name": "a", "rules": [{"nope": ["x"]}]}, {"name": "b", "rules": "x"}, {"name": "c", "rules": [], "posted": "soon"}]
        refused = [stops(lambda t=t: bank.add_topic(t, path), t["name"]) for t in bad]
        same = path.read_bytes() == before and [p.name for p in Path(tmp).iterdir()] == ["posted_topics.json"]
        bank.add_topic({"name": "d", "type": "crash", "posted": "2026-11-24", "rules": [{"words": ["x"]}]}, path)
        added = launch.load_posted(path, log=quiet)[-1]["name"]
    return refused == [True] * 3 and same and added == "d", (refused, same)


@check
def posting_locks_the_footage_before_it_writes_the_topic():
    """B2: mark_posted wrote the topic first, so when locking the footage failed, the Short counted as posted and its
    footage stayed free. Now the footage is locked first: when that fails nothing is added to posted_topics.json, and when
    the topic can't be written the footage is locked already; run again, it finishes"""
    import stat
    with tempfile.TemporaryDirectory() as tmp:
        lib, shorts, bdir = bank_of(tmp, {"GTA_20261119120000": crash_at(300)}, used={"shorts": []})
        posted = Path(tmp) / "posted_topics.json"
        shutil.copyfile(launch.POSTED_FILE, posted)
        m = next(x for x in build(lib, shorts, bdir, StandIn("Player's car crashes into a wall", crash=True))["moments"]
                 if x["main"] == "crash")
        kw = {"shorts_dir": shorts, "bank_dir": bdir, "posted_file": posted, "log": quiet, "on": "2026-11-24"}
        before, real = posted.read_bytes(), footage_short.set_status

        def broken(*a, **k):
            raise OSError("the disk is full")
        footage_short.set_status = broken
        try:
            bank.mark_posted(m["id"], "car into a wall", **kw)
            failed = False
        except (OSError, bank.BankError):
            failed = True
        finally:
            footage_short.set_status = real
        nothing = posted.read_bytes() == before and launch.all_used_spans(shorts) == []
        os.chmod(posted, stat.S_IREAD)                    # the topic can't be written now
        try:
            bank.mark_posted(m["id"], "car into a wall", **kw)
            refused = False
        except (OSError, bank.BankError):
            refused = True
        finally:
            os.chmod(posted, stat.S_IWRITE | stat.S_IREAD)
        locked = launch.all_used_spans(shorts) == [(m["video"], m["start_s"], m["end_s"])]
        untouched = posted.read_bytes() == before
        bank.mark_posted(m["id"], "car into a wall", **kw)
        done = launch.load_posted(posted, log=quiet)[-1]["name"] == "car into a wall"
        left = [p.name for p in Path(tmp).glob("posted_topics*")]
    return failed and nothing and refused and locked and untouched and done and left == ["posted_topics.json"], (
        failed, nothing, refused, locked, untouched, done, left)


def unsure_crash():
    """(recording, moment): mined as a crash; the peak's look ticks one and both lines say it, but nothing loud is heard."""
    rec = made_up(lines={300.0: ("Player's car crashes into a wall", "driving")}, loud={303.0: 1.2})
    m = labelled(rec, 301, [look(302, 306, "A car crashes into a wall", crash=True)])
    return rec, m


@check
def an_unsure_crash_is_no_crash_to_the_picker_or_the_words():
    """B3: a moment whose label is "unsure" kept the crash the safety answers saw at its peak: the evidence had a crash
    event, the quality gate a crash payoff (tier 3) and the words "crash" passed. Now an unsure label keeps no impact: no
    event, no payoff, the word refused; a sure crash is as it was"""
    rec, m = unsure_crash()
    sure = dict(m, label=dict(m["label"], kind="crash", sure=True))
    out = []
    for x in (m, sure):
        c = dict(x, bank_id=x["id"], id="M1", terms=dict(x["terms"]), verified={"crash": [[302.0, 306.0]], "police": [[302.0, 306.0]]})
        bank.keep_what_the_peak_shows(c)
        launch.verify_terms(rec, c)
        launch.story_gate(rec, c, "gta6", False)
        items = launch.evidence(rec, c["start_s"], c["end_s"], verified=c["verified"])
        out.append((m["label"]["kind"] == "unsure", "crash" in c["verified"], [e["value"] for e in items if e["kind"] == "event"],
                    c["point"]["action_payoff"], launch.payoff_strength(c)[0], launch.moment_type(c) == "crash",
                    bool(truth.word_problems("That crash was huge", items, False, "x")), "police" in c["verified"]))
    unsure_, sure_ = out
    return (unsure_[1:] == (False, [], [], unsure_[4], False, True, True) and unsure_[4] < 3
            and sure_[1:] == (True, ["crash"], ["crash"], 3, True, False, True)), out


@check
def an_unsure_impact_is_not_on_the_action_shelf():
    """B3: 9 of the 14 unsure moments on the real action shelf were impacts their own peak doesn't show (crash, explosion,
    fall). An unsure impact or shootout is a list item; a sure one, an unsure chase and loud action stay action clips"""
    _, m = unsure_crash()
    sure = dict(m, label=dict(m["label"], kind="crash", sure=True))
    chase = dict(m, main="chase", kinds=["chase"])
    loud = dict(m, main="loud", kinds=["loud"])
    both = dict(m, main="explosion", kinds=["explosion", "loud"])
    shelves = [bank.shelf_of(dict(x, questions=[])) for x in (m, sure, chase, loud, both, dict(m, main="fall", kinds=["fall"]),
                                                              dict(m, main="shooting", kinds=["shooting"]))]
    return shelves == ["list", "action", "action", "action", "list", "list", "list"], shelves


GUN_LINE = "Player aims gun, shooting at a man near a bridge"


@check
def gun_words_need_shooting_the_frames_show():
    """B4: launch.EVENTS has no shooting, so a line like "Player aims gun, shooting at a man" let a script say "shoot", "gun"
    and "shootout" though the vision check saw no shooting. Now those words need shooting seen in the frames; a line that
    only names a gun marks it not seen; where shooting is seen they are fine; without the vision check nothing changes"""
    rec = made_up(lines={300.0: (GUN_LINE, "on_foot")}, segs=[seg(0, 600, "on_foot")])
    unseen = launch.evidence(rec, 295.0, 310.0, verified={})
    seen = launch.evidence(rec, 295.0, 310.0, verified={"shooting": [[301.0, 305.0]]})
    plain = launch.evidence(rec, 295.0, 310.0)
    said = ("Watch the shootout", "You shoot first", "A gun comes out", "They start firing")
    blocked = [bool(truth.word_problems(s, unseen, False, "x")) for s in said]
    free = [bool(truth.word_problems(s, e, False, "x")) for s in said[:3] for e in (seen, plain)]
    marked = [e.get("not_seen") for e in unseen if e["kind"] == "seen" and "gun" in e["value"]]
    return all(blocked) and not any(free) and marked == [["shootout"]], (blocked, free, marked)


@check
def words_nobody_verified_name_no_topic():
    """B4: what a moment repeats, and the questions it answers, were read from lines the frames didn't confirm. A line that
    names a crash, jump, explosion, shooting or police the safety answers (or, in the bank, the moment's own label) didn't
    see is left out; the same line confirmed counts"""
    rec = made_up(lines={300.0: ("Player's car crashes into a wall near an overpass", "driving"),
                         306.0: ("Player drives past a beach", "driving")}, loud={303.0: 1.2})
    base = {"start_s": 296.0, "end_s": 314.0, "video": rec["stem"]}
    texts = [launch.moment_text(rec, dict(base, **x)) for x in (
        {"verified": {}}, {"verified": {"crash": [[301.0, 305.0]]}}, {"label": {"kind": "unsure", "sure": False}},
        {"label": {"kind": "crash", "sure": True}}, {})]
    qs = [{"id": "f1-sports-car", "kind": "experiment", "question": "x", "rows": [(rec["stem"], 290.0, 320.0)]},
          {"id": "f1-night-rain-drive", "kind": "experiment", "question": "x", "rows": [(rec["stem"], 290.0, 320.0)]}]
    rec2, m = unsure_crash()
    rec2["sheets"][51]["notable"] = "Car crashes in the rain at night"
    ids = [[q["id"] for q in bank.answers_question(x, qs, rec2)] for x in (
        m, dict(m, label=dict(m["label"], kind="crash", sure=True)),
        dict(m, kinds=["crash", "star rise"], label={"kind": "star rise", "sure": True}))]   # (the rain is only in the crash's line)
    cop = made_up(lines={300.0: ("Police chase the player", "driving"), 306.0: ("Player drives past a beach", "driving")})
    topics = []
    for seen in ({}, {"police": [[300.0, 306.0]]}):
        asked = []
        launch.story_gate(cop, {"start_s": 296.0, "end_s": 326.0, "span": [0.0, 600.0], "terms": {}, "video": cop["stem"],
                                "verified": seen}, "gta6", False, lambda t, labels, words: asked.append(t[0]))
        topics.append(asked)
    return (["overpass" in t for t in texts] == [False, True, False, True, True] and all("beach" in t for t in texts)
            and [sorted(x) for x in ids] == [[], ["f1-night-rain-drive", "f1-sports-car"], []]
            and topics == [["the beach"], ["the beach", "police chases"]]), (texts, ids, topics)


@check
def footage_that_overlaps_posted_or_rejected_is_never_offered():
    """B8: a moment 27 % inside a rejected span, or with a third of a second of posted footage in its middle, was "new" and
    was offered (its neighbours are only trimmed at the edges), so it failed late, at the render. Now any overlap of more
    than a frame or two keeps it out; footage next to it still only shortens its stretch; a status on the page is as before"""
    rec, m = unsure_crash()
    m = dict(m, label=dict(m["label"], kind="crash", sure=True), shelf="action", id="x", status="new")
    a, b = m["start_s"], m["end_s"]
    def offered(**spans):
        full = {"posted": [], "rendered": [], "rejected": [], **spans}
        got = bank.candidates([m], {rec["stem"]: rec}, {k: [(rec["stem"], *s, "n") for s in v] for k, v in full.items()})
        return got[0]["span"] if got else None
    return (offered() == [0.0, 600.0] and offered(rejected=[(a + 4, a + 7)]) is None and offered(posted=[(a + 5, a + 5.3)]) is None
            and offered(posted=[(a - 10, a + 0.4)]) is None and offered(rejected=[(b - 0.4, b + 9)]) is None
            and offered(posted=[(a - 10, a)], rejected=[(b, b + 5)]) == [round(a, 2), round(b, 2)]
            and offered(rendered=[(a, b)]) == [0.0, 600.0]
            and bank.status_of(m, {"posted": [], "rendered": [], "rejected": [(rec["stem"], a + 4, a + 7, "n")]})[0] == "new")


@check
def a_proof_short_keeps_its_topic_as_its_type():
    """B9: verify_terms rewrote a topic candidate's category ("topic: heat-icons") from its points, so "no Short of the same
    type as one of the last 2 posted" never saw a heat-icons Short after a heat-icons Short. The category stays; the picker
    takes the other type; a proof moment marked posted is written down as its topic"""
    rec = made_up()
    m = {"id": "A", "video": rec["stem"], "path": rec["path"], "start_s": 100.0, "end_s": 130.0, "span": [0.0, 600.0],
         "session_s": 115.0, "score": 9.0, "terms": {"star_rise": 4.0, "police": 0.5, "topic": 1.5, "hud6": 1.0},
         "category": "topic: heat-icons", "topic": {"id": "heat-icons", "why": "x", "at_s": 115.0, "facts": []},
         "verified": {"police": [[100.0, 130.0]]}}
    other = dict(m, id="B", category="star rise", score=2.0, terms={"star_rise": 2.5, "police": 0.5}, start_s=400.0, end_s=420.0,
                 verified={"police": [[400.0, 420.0]]})
    other.pop("topic")
    for x in (m, other):
        launch.verify_terms(rec, x)
    posted = [{"name": "cops forget you", "type": "heat-icons", "posted": "2026-11-23", "rules": [{"words": ["zzz"]}]}]
    stars = [{"name": "stars", "type": "star rise", "posted": "2026-11-23", "rules": [{"words": ["zzz"]}]}]
    picks = [launch.pick_varied([dict(m, score=9.0), dict(other)], 1, p, lambda x: "", quiet)[0]["id"] for p in ([], posted, stars)]
    wanted = launch.moment_type({"topic": {"id": "wanted-system"}, "terms": {"star_rise": 4.0}, "category": "topic: wanted-system"})
    bank_m = {"label": {"kind": "star rise", "sure": True}, "kinds": ["heat icons", "star rise"], "shelf": "proof", "questions": [],
              "category": "star rise", "terms": {"star_rise": 4.0}}
    return (m["category"] == "topic: heat-icons" and launch.moment_type(m) == "heat-icons" and other["category"] == "star rise"
            and picks == ["A", "B", "A"] and wanted == "star rise" and bank.type_of_moment(bank_m) == "heat-icons"
            and bank.type_of_moment(dict(bank_m, shelf="action")) == "star rise"), (m["category"], picks)


@check
def rejecting_a_moment_keeps_every_earlier_rejection():
    """B10: mark_rejected threw out an earlier rejection that had the same id, though it was another recording's or, after a
    rebuild, another moment's: that footage was offered again. Both stay; the same moment rejected twice is one entry"""
    with tempfile.TemporaryDirectory() as tmp:
        lib, shorts, bdir = bank_of(tmp, {"GTA_20261119120000": crash_at(300)}, used={"shorts": []})
        b = build(lib, shorts, bdir, StandIn("Player's car crashes into a wall", crash=True))
        m = next(x for x in b["moments"] if x["main"] == "crash")
        old = [{"id": m["id"], "spans": [["rec_other", 10.0, 20.0]], "on": "2026-11-20", "label": "crash"},
               {"id": m["id"], "spans": [[m["video"], 500.0, 510.0]], "on": "2026-11-21", "label": "crash"}]
        (shorts / footage_short.USED_FOOTAGE).write_text(json.dumps({"shorts": [], "rejected": old}), encoding="utf-8")
        for why in ("dull", "still dull"):
            bank.mark_rejected(m["id"], why, shorts, bdir, quiet)
        rows = footage_short.load_used(shorts)["rejected"]
        locked = launch.all_used_spans(shorts)
    return (rows[:2] == old and len(rows) == 3 and rows[2]["why"] == "still dull" and ("rec_other", 10.0, 20.0) in locked
            and (m["video"], m["start_s"], m["end_s"]) in locked), rows


@check
def ids_are_unique_and_stay_with_their_moment_on_a_rebuild():
    """B10: an id is the second a moment starts, so a rebuild whose moment starts a little earlier gave it another id (its
    rendered Short was lost to it), a new moment could take an old one's id, and two recordings of the same second shared
    theirs. Rebuilt, a moment keeps the id of the old one on its seconds; every id in the bank is one of a kind"""
    stems = ("GTA_20261119120000", "PS5_20261119120000")
    with tempfile.TemporaryDirectory() as tmp:
        lib, shorts, bdir = bank_of(tmp, {s: crash_at(102, 300) for s in stems}, used={"shorts": []})
        ai = StandIn("Player's car crashes into a wall", only=CRASHES, crash=True)
        first = build(lib, shorts, bdir, ai)
        ids = [m["id"] for m in first["moments"]]
        m = next(x for x in first["moments"] if x["main"] == "crash" and x["video"] == stems[0] and x["start_s"] > 200)
        other = next(x for x in first["moments"] if x["main"] == "crash" and x["video"] == stems[0] and x["start_s"] < 200)
        # the last build saw it start 1.4 s earlier, under another id; the other crash's id was a moment's that is gone
        saved = bank.read_bank(bdir)
        for x in saved["moments"]:
            if x["id"] == m["id"]:
                x.update(id="1119-120000-was", start_s=x["start_s"] - 1.4)
            elif x["id"] == other["id"]:
                x.update(family="gone", start_s=550.0, end_s=560.0)
        index.write_json(bdir / bank.BANK_FILE, saved)
        second = build(lib, shorts, bdir, ai)
        now, new = (next(x for x in second["moments"] if x["video"] == stems[0] and x["start_s"] == y["start_s"] and x["main"] == "crash")
                    for y in (m, other))
        ids2 = [x["id"] for x in second["moments"]]
        third = build(lib, shorts, bdir, ai)
    return (len(set(ids)) == len(ids) and len(set(ids2)) == len(ids2) and now["id"] == "1119-120000-was" and m["id"] not in ids2
            and new["id"].startswith(other["id"]) and other["id"] not in ids2
            and [x["id"] for x in third["moments"]] == ids2), (now["id"], new["id"], m["id"] in ids2)


@check
def a_rendered_short_belongs_to_the_moment_it_shows():
    """B10: a Short's bank_id was trusted on its own, so after a rebuild that gave the id to another moment, marking that
    moment posted locked the wrong footage. A Short counts for a moment only when it shows some of its seconds"""
    with tempfile.TemporaryDirectory() as tmp:
        lib, shorts, bdir = bank_of(tmp, {"GTA_20261119120000": crash_at(300)}, used={"shorts": []})
        m = next(x for x in build(lib, shorts, bdir, StandIn("Player's car crashes into a wall", crash=True))["moments"]
                 if x["main"] == "crash")
        for run, span in (("launch-a", [["GTA_20261119120000", 20.0, 35.0]]), ("launch-b", [["GTA_20261119120000", 296.0, 312.0]])):
            (shorts / run).mkdir()
            (shorts / run / "render.json").write_text(json.dumps({"shorts": [
                {"short": 1, "status": "READY", "bank_id": m["id"], "folder": str(shorts / run / "1-x"), "title": "x", "shown": span}]}),
                encoding="utf-8")
        got = [name for name, _, _ in bank.shorts_of(shorts, m)]
    return got == ["1-x@launch-b"], got


@check
def a_crash_nobody_measured_the_sound_of_is_unsure():
    """B11: with no loudness (an empty list, or all zeros: clip.py never ran on it) the loudness check was skipped, so a tick
    and a line were a sure crash. Not measured is not heard: unsure, and it says so; an explosion the same"""
    say = [look(302.0, 306.0, "A car crashes on highway, then an explosion.", crash=True, explosion=True)]
    out = []
    for loud in ([], [0.0] * 1200):
        rec = made_up(lines={300.0: ("Red car crashes, then an explosion", "driving")}, loud={303.0: 3.0})
        m = one(rec, 301)
        rec["raw"]["loud"] = list(loud)
        out.append(bank.label_of(m, rec, say))
    rec = made_up(lines={300.0: ("Red car crashes, then an explosion", "driving")}, loud={303.0: 3.0})
    heard = bank.label_of(one(rec, 301), rec, say)
    return (all(x["kind"] == "unsure" and "sound" in x["why"] and "measured" in x["why"] for x in out)
            and heard["kind"] == "crash" and heard["sure"]), [(x["kind"], x["why"]) for x in out + [heard]]


@check
def a_quiet_crash_does_not_hide_what_else_the_peak_shows():
    """B11: a crash nothing loud is heard of was "unsure" at once, before the jump, the fall, the shooting or the HUD change
    at the same peak was looked at. Those are the label where they hold; with nothing else there it stays unsure, with why"""
    def label(note, line, counts=None, **yes):
        rec = made_up(lines={300.0: (line, "driving")}, loud={303.0: 1.2}, counts=counts)
        return bank.label_of(one(rec, 301), rec, [look(302.0, 306.0, note, crash=True, **yes)])
    quiet_ = label("A car crashes", "Player's car crashes")
    jump = label("A car crashes after a jump off a ramp", "Car jumps off a ramp and crashes", stunt=True)
    fall = label("A man falls as a car crashes", "A car crashes and a man falls")
    shots = label("A car crashes, man shooting", "Car crashes while shooting", shooting=True)
    stars = label("A car crashes", "Player's car crashes", counts=[0] * 606 + [2] * 60 + [0] * 534)
    kinds = [x["kind"] for x in (quiet_, jump, fall, shots, stars)]
    return (kinds == ["unsure", "stunt", "fall", "shooting", "star rise"] and "loud" in quiet_["why"]
            and all(x["sure"] for x in (jump, fall, shots, stars))), kinds


@check
def a_crash_that_is_only_ticked_names_nothing_else():
    """B11: a crash the look ticks but no line says fell through to softer labels: "someone reacts", "police chase", "first
    look" or "driving", sure. It is unsure: nobody knows what that peak shows; a HUD change there is still the HUD's"""
    def label(line, note, t=301, segs=None, counts=None, kinds=None, main=None):
        rec = made_up(lines={300.0: (line, "driving")}, loud={303.0: 2.5}, segs=segs, counts=counts)
        ms = bank.mine_all(rec)[0]
        m = next(x for x in ms if x["start_s"] <= 303 <= x["end_s"] and (kinds is None or x["family"] in kinds))
        m["peak_s"] = 303.0
        return bank.label_of(m, rec, [look(302.0, 306.0, note, crash=True)])
    reaction = label("A man argues with the player", "A man argues with the player", kinds=("reaction",))
    chase = label("Player drives fast", "Player drives fast in traffic", segs=[seg(0, 290, "driving"), seg(290, 320, "chase", stars=2),
                                                                                seg(320, 600, "driving")], counts=[2] * 1200)
    calm = label("Player drives down a street", "Player drives a car down a street", kinds=("calm",))
    hud = label("Player drives", "Player drives a car", counts=[0] * 606 + [2] * 60 + [0] * 534)
    return ([x["kind"] for x in (reaction, chase, calm)] == ["unsure"] * 3 and all("ticks a crash" in x["why"] for x in (reaction, chase, calm))
            and hud["kind"] == "star rise"), [x["kind"] for x in (reaction, chase, calm, hud)]


@check
def an_empty_build_never_replaces_a_full_bank():
    """B12: with every recording of the library gone (a drive not plugged in), prepare built a bank of nothing and wrote it
    over the real one. It stops with a plain error and content_bank.json and bank.html keep every byte; a first, empty bank
    may be written"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        lib, shorts, bdir = bank_of(tmp, {"GTA_20261119120000": crash_at(300)}, used={"shorts": []})
        build(lib, shorts, bdir, StandIn("Player's car crashes into a wall", crash=True))
        before = [(bdir / f).read_bytes() for f in (bank.BANK_FILE, bank.PAGE_FILE)]
        (tmp / "GTA_20261119120000.webm").unlink()
        inp = tmp / "input"
        inp.mkdir()
        keep, launch.POSTED_FILE = launch.POSTED_FILE, tmp / "none.json"
        try:
            stopped = stops(lambda: launch.prepare("gta6", day="2026-11-24", bank_dir=bdir, input_dir=inp, shorts_dir=shorts,
                                                   library_dir=lib, log=quiet, check=False, sheets=False, safety=None,
                                                   posted_file=tmp / "none.json"), "moments")
        finally:
            launch.POSTED_FILE = keep
        same = [(bdir / f).read_bytes() for f in (bank.BANK_FILE, bank.PAGE_FILE)] == before
        fresh = bank.build(lib, shorts, tmp / "new", None, {"topics": []}, hud6=lambda *a, **k: [], log=quiet)
    return stopped and same and fresh["moments"] == [], (stopped, same)


def main():
    ok = True
    for fn in testkit.selected(CHECKS):
        try:
            got = fn()
            good, err = (bool(got[0]), f"  (got {got[1]})") if isinstance(got, tuple) else (bool(got), "")
            err = err if not good else ""
        except Exception as e:
            good, err = False, f"  ({type(e).__name__}: {e})"
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'} {fn.__name__}: {' '.join((fn.__doc__ or '').split())}{err}", flush=True)
    print(f"RESULT: {'PASS' if ok else 'FAIL'} ({len(testkit.selected(CHECKS))} checks)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
