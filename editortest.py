"""Checks for editor-v3 (engine v4, Nov 20): payoff-first editing (cutplan.py), the measured picture (steady.py), the
Short put together (assemble.py) and launch.py's render_v4, one check per owner rule. The voice is made up (a tone per
word) and so is the vision model, so neither Kokoro nor Ollama is needed; the end-to-end check renders a real Short
from a made-up video with ffmpeg.

  .venv-tts\\Scripts\\python.exe editortest.py
"""
import functools
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import cutplan
import launch
import steady
import testkit
import truth
from launchtest import FACTS, TODAY, made_up_idx, run_folder_with

HERE = Path(__file__).resolve().parent
CHECKS = []
_TMP = tempfile.TemporaryDirectory()
ROOT = Path(_TMP.name)          # what the end-to-end checks share (one recording, two renders), made once a run


def check(fn):
    CHECKS.append(fn)
    return fn


# --- Made-up moments, shots and lines ---

WINDOW = [10.0, 40.0]
EVIDENCE = [{"id": "E1", "kind": "activity", "value": "driving", "from_s": 10.0, "to_s": 40.0, "source": "made up"},
            {"id": "E2", "kind": "event", "value": "crash", "from_s": 20.0, "to_s": 23.0, "at_s": 21.0,
             "source": "made up"}]


def moment(path="x.webm"):
    return {"short": 1, "id": "M1", "type": "action", "video": "synth", "path": str(path), "window": list(WINDOW),
            "mode": "hud", "evidence": [dict(e) for e in EVIDENCE], "flags": [], "type_why": "made up"}


def short(**over):
    s = {"short": 1, "moment": "M1", "type": "action", "format": "A1", "format_why": "a crash: setup, then the hit",
         "payoff": {"from": 20.0, "to": 22.5, "what": "the car slams into the wall"},
         "hooks": ["Watch this crash", "This crash came fast", "Wait for the crash"], "star": 0,
         "key_verb": "watch",                                                 # Nov 26: the hook, card and title share it
         "title": "WATCH this GTA 6 car on the highway",                      # (was "This GTA 6 CRASH came out of nowhere")
         "description": "A car speeds down the road. Then it crashes.",
         "hashtags": ["#GTA6", "#GTAVI", "#Shorts"],
         "shots": [{"from": 14.0, "to": 25.0}, {"from": 30.0, "to": 36.0}],
         "lines": [{"say": "Watch closely.", "claim": "hype", "at": 14.0},        # Nov 26: "Watch this." is filler now
                   {"say": "Hold for it.", "claim": "hype", "at": 17.5},          # PR 34: "Wait for it." is filler now
                   {"say": "That crash was huge.", "claim": "on_screen", "evidence": ["E2"],
                    "teaches": "the crash is the point", "at": 23.0},
                   {"say": "You just keep going.", "claim": "hype", "at": 34.8}]}  # PR 30: ends the Short (was 30.3)
    s.update(over)
    return s


def secs(s):
    return [0.28 * len(cutplan.words_of(l["say"])) for l in s["lines"]]


def problems_of(s, m=None, durations="auto", loudest=21.0, cuts=(), hud=()):
    m = m or moment()
    _, problems, _ = cutplan.plan(s, m, secs(s) if durations == "auto" else durations, loudest, cuts, hud)
    return problems


def has(problems, text):
    return any(text in p for p in problems)


# --- 1. Payoff first (and why Short 3 lost its crash) ---

@check
def a_good_plan_passes():
    """the made-up action Short keeps every rule: payoff whole in shot 1, 17 s, lines before their cuts"""
    p, problems, _ = cutplan.plan(short(), moment(), secs(short()), 21.0)
    return not problems and p["total_s"] == 17.0 and len(p["placed"]) == 4 and p["payoff"]["what"]


@check
def no_payoff_no_short():
    """the interest gate: a Short that names no payoff (or a vague one) is rejected, with the reason"""
    a = problems_of(short(payoff=None))
    b = problems_of(short(payoff={"from": 20.0, "to": 22.5, "what": "crash"}))
    c = problems_of(short(payoff={"from": 20.0, "to": 31.0, "what": "a very long payoff here"}))
    return has(a, "No clear payoff, no Short") and has(b, "3+ words") and has(c, "lasts 11.0 s")


@check
def the_payoff_plays_whole_in_one_shot():
    """a payoff split over two shots (or cut short) is refused: it plays whole, never cut"""
    cut = short(shots=[{"from": 14.0, "to": 21.0}, {"from": 21.0, "to": 25.0}, {"from": 30.0, "to": 36.0}])
    return has(problems_of(cut), "must play whole inside one shot")


@check
def short3_payoff_must_hold_the_hit():
    """Short 3 of part3: the old beats showed 267.0-268.5 and 270.0-271.5 while the hit (the loudest sound) was at
    269.0-269.6; a payoff on the camera switch (270.0) misses it, the impact itself holds it"""
    old = cutplan.loudest_problems({"from": 270.0, "to": 271.5, "what": "x"}, 269.6)
    new = cutplan.loudest_problems({"from": 268.6, "to": 270.2, "what": "x"}, 269.6)
    beats = [(267.0, 267.0 + 1.533), (270.0, 270.0 + 1.533)]
    shown = any(a <= 269.6 <= b for a, b in beats)
    return has(old, "misses the loudest moment") and not new and not shown


@check
def the_setup_comes_first():
    """a payoff at frame 0 is refused: the viewer sees at least 1.5 s of setup"""
    s = short(shots=[{"from": 20.0, "to": 25.0}, {"from": 26.0, "to": 40.0}],
              lines=[{"say": "Wait for it.", "claim": "hype", "at": 26.5}])
    return has(problems_of(s), "show at least 1.5 s of setup")


# --- 2. No padding: no second twice, no loop ending ---

@check
def no_second_is_shown_twice():
    """two shots that share seconds are refused (Short 3 showed 266.3-267.4 three times)"""
    s = short(shots=[{"from": 14.0, "to": 25.0}, {"from": 24.0, "to": 31.0}])
    p = problems_of(s)
    return has(p, "never show a second twice") or has(p, "both show")


@check
def too_little_footage_makes_it_shorter_not_repeated():
    """under 15 s of unique footage: refused with "add unique footage, never repeat", nothing is looped"""
    s = short(shots=[{"from": 14.0, "to": 25.0}], lines=short()["lines"][:3])
    return has(problems_of(s), "never repeat a second")


@check
def v4_has_no_loop_ending():
    """engine v4 needs no loop line (the loop ending replayed the first shot); v3 still does"""
    lines = short()["lines"]
    v4 = truth.story_problems(short(), moment(), FACTS, "v4")
    v3 = truth.story_problems(short(), moment(), FACTS, "v3")
    return not has(v4, "loop") and has(v3, "loop") and lines


@check
def assembled_visuals_show_each_shot_once():
    """the finished timeline (assemble.visuals) shows every shot's seconds once, in order, nothing after the last"""
    import assemble
    p, _, _ = cutplan.plan(short(), moment(), secs(short()), 21.0)
    for s in p["shots"]:
        s["clip"] = f"shot{s['n']}.mp4"
    vis = assemble.visuals(p["shots"], p["placed"], p["total_s"])
    recs = [(v["rec_s"], v["rec_s"] + v["seconds"]) for v in vis]
    ordered = all(b[0] >= a[1] - 1e-3 or (vb["shot"] != va["shot"]) for (a, va), (b, vb)
                  in zip(zip(recs, vis), zip(recs[1:], vis[1:])))
    total = sum(v["seconds"] for v in vis)
    return ordered and abs(total - p["total_s"]) < 0.01 and len({v["shot"] for v in vis}) == 2


# --- 3. A steady picture: one fixed window, stabilised only where it measures calmer ---

@check
def the_window_is_fixed_and_the_same_size():
    """every shot is one fixed 9:16 window (a plain crop, no moving keys), 5 % in, and the stabilised piece keeps
    the same centre and size"""
    info = {"width": 1920, "height": 1080, "container_crop": None, "video_codec": "vp9", "color_range": "tv"}
    static, window, keep = steady.geometry(info, 0.5)
    left = steady.geometry(info, 0.0)[0]
    return (static == "crop=578:1028:671:26" and window == "crop=760:1080:580:0" and keep == "crop=608:1080:76:0"
            and "'" not in static and left.startswith("crop=578:1028:91:"))


@check
def stabilise_only_when_it_helps():
    """the rule measured on Nov 20: calm driving stays as it is; the impact jolt (2.1/8.8 px) is kept stabilised
    (1.5/2.0); a stabilised piece with a black edge or no gain is dropped"""
    k = steady.keep_stabilised
    return (not k((1.3, 1.0, False), (3.0, 2.0, False)) and k((2.1, 8.8, False), (1.5, 2.0, False))
            and not k((2.1, 8.8, False), (1.5, 2.0, True)) and not k((7.4, 5.4, False), (6.0, 5.2, False)))


@check
def pieces_split_at_the_game_camera_cuts():
    """a shot is split where the game switches camera, each piece ending on the frame before the cut (Phase 1: 58
    frames from 268.333 showed one frame of the next camera; 57 is right)"""
    return (steady.split_points(268.3333, 70, [270.247, 270.747]) == [57, 13]
            and steady.split_points(265.0, 198, [270.247, 270.747]) == [157, 15, 26]
            and steady.split_points(10.0, 30, []) == [30])


@check
def a_fast_camera_swing_is_not_a_cut_and_the_payoff_is_judged_apart():
    """a run of scene spikes (the camera swinging round as you get out) is not a camera cut, a lone spike is; and a
    shot is split just before its payoff, so the calm setup can stay as it is while the jolt is steadied (one piece
    for both kept the jolt, Nov 20)"""
    run = [279.823, 279.856, 279.89, 279.923, 279.956, 279.99]
    lone = steady.lone_spikes([270.247, 270.747] + run)
    payoff_split = steady.split_points(265.0, 198, [270.247, 270.747, 268.6 - steady.PAYOFF_SPLIT_S])
    return lone == [270.247, 270.747] and payoff_split == [99, 58, 15, 26]


def make_video(path, seconds=8, blur_until=0.0, shake=0, loud_at=None, size="640x360"):
    """A made-up recording: testsrc2 with a fixed fine texture (testsrc2 alone is mostly flat colour, unlike
    gameplay), optionally shaking (a window moving over a bigger picture), blurred until blur_until, with a quiet
    tone and a loud burst at loud_at."""
    w, h = (int(x) for x in size.split("x"))
    src = f"testsrc2=size={w + 2 * shake}x{h + 2 * shake}:rate=60:duration={seconds}"
    vf = "noise=alls=40:allf=u"
    if shake:
        vf += f",crop={w}:{h}:'{shake}+{shake}*sin(n*2.1)':'{shake}+{shake}*cos(n*1.7)'"
    if blur_until:
        vf += f",boxblur=24:4:enable='lt(t,{blur_until})'"
    vf += ",format=yuv420p"
    vol = f"volume='if(between(t,{loud_at - 0.3},{loud_at + 0.3}),1.0,0.05)':eval=frame" if loud_at else "volume=0.05"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src,
                    "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={seconds}", "-vf", vf,
                    "-af", vol, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-c:a", "aac",
                    "-shortest", str(path)], check=True, capture_output=True)
    return path


@check
def shake_is_measured_and_judder_scores_high():
    """the jitter measure tells a juddering picture from a steady one (made-up footage)"""
    with tempfile.TemporaryDirectory() as tmp:
        calm = make_video(Path(tmp) / "calm.mp4", 2)
        shaky = make_video(Path(tmp) / "shaky.mp4", 2, shake=12)
        a = steady.shake_of(steady.gray_frames(calm))
        b = steady.shake_of(steady.gray_frames(shaky))
        return max(a[:2]) < 1.0 and max(b[:2]) > 4.0


# --- 4. The first frame ---

@check
def a_blurred_first_frame_is_found_and_the_start_moves():
    """frame 0 under a blur (Short 2's leaf) fails; the start moves to the first clear frame, about 0.6 s later"""
    with tempfile.TemporaryDirectory() as tmp:
        v = make_video(Path(tmp) / "leaf.mp4", 4, blur_until=0.6)
        start, why, tried = steady.clear_start(v, 0.0, 3.0)
        sharp, why2, _ = steady.clear_start(v, 1.0, 3.0)
        return not why and 0.55 <= start <= 0.8 and tried[0]["why"] and sharp == 1.0 and not why2


@check
def a_hook_about_the_hud_needs_its_proof_at_frame_0():
    """Short 2 said "six stars" over a leaf: a hook naming the stars needs shot 1 to open on their close-up"""
    hud = set(launch.HUD_NOUNS)
    bad = short(hooks=["Six stars, count them", "x y z", "a b c"], star=0)
    good = short(hooks=["Six stars, count them", "x y z", "a b c"], star=0,
                 shots=[{"from": 14.0, "to": 25.0, "inset": {"zoom": "stars", "start": 0.0, "length": 1.8}},
                        {"from": 30.0, "to": 36.0}])
    return has(problems_of(bad, hud=hud), "show it from frame 0") and not has(problems_of(good, hud=hud), "frame 0")


# --- 5. Cuts on sentence ends and events ---

@check
def a_line_ends_before_its_cut():
    """a line too long for its shot is refused (the voice never runs over a cut); one that fits ends 0.05 s before"""
    long_line = short(lines=[{"say": "Watch this.", "claim": "hype", "at": 24.8}] + short()["lines"][3:])
    p, problems, _ = cutplan.plan(short(), moment(), secs(short()), 21.0)
    cut = p["shots"][0]["end"]
    first = [x for x in p["placed"] if x["shot"] == 1]
    return has(problems_of(long_line), "past the cut") and all(x["end"] <= cut - cutplan.CUT_ROOM_S for x in first)


@check
def a_cut_lands_on_a_sentence_end_or_an_event():
    """a cut long after the last line (on a timer) is refused; right after a line, or on an event, it passes"""
    shots = [{"n": 1, "from": 0.0, "to": 6.0, "start": 0.0, "end": 6.0}, {"n": 2, "from": 9.0, "to": 12.0,
                                                                         "start": 6.0, "end": 9.0}]
    timer = cutplan.cut_problems(shots, [{"line": 1, "start": 0.2, "end": 2.0}])
    sentence = cutplan.cut_problems(shots, [{"line": 1, "start": 4.0, "end": 5.6}])
    event = cutplan.cut_problems(shots, [{"line": 1, "start": 0.2, "end": 2.0}], events=[9.1])
    return has(timer, "neither a sentence end nor an event") and not sentence and not event


@check
def a_cut_on_the_6_s_sampling_grid_is_not_an_event():
    """the vision model's sheets, activity segments and event windows sit on a 6 s grid: a cut 3 s after the last
    line that lands on a sheet edge is a cut on a timer and fails; a HUD reading's change or a camera cut there passes;
    another recording's compare item never counts"""
    m = {"evidence": [{"id": "E1", "kind": "seen", "value": "x", "from_s": 54.0, "to_s": 60.0},
                      {"id": "E2", "kind": "activity", "value": "driving", "from_s": 48.0, "to_s": 60.0},
                      {"id": "E3", "kind": "event", "value": "crash", "from_s": 54.0, "to_s": 60.0},
                      {"id": "C1", "kind": "compare", "value": "GTA 5: chase", "from_s": 59.9, "to_s": 70.0}]}
    shots = [{"n": 1, "from": 50.0, "to": 60.0, "start": 0.0, "end": 10.0},
             {"n": 2, "from": 66.0, "to": 70.0, "start": 10.0, "end": 14.0}]
    placed = [{"line": 1, "start": 1.0, "end": 7.0}]
    timer = cutplan.cut_problems(shots, placed, cutplan.events_of(m, None))
    hud = dict(m, evidence=m["evidence"] + [{"id": "E4", "kind": "stars", "value": "2", "from_s": 60.1, "to_s": 64.0}])
    on_hud = cutplan.cut_problems(shots, placed, cutplan.events_of(hud, None))
    on_camera = cutplan.cut_problems(shots, placed, cutplan.events_of(m, None, camera_cuts=[59.8]))
    return has(timer, "neither a sentence end nor an event") and not on_hud and not on_camera


@check
def no_voice_over_an_action_payoff():
    """in an action Short the game's own sound plays the payoff: a line over it is refused"""
    s = short(lines=[{"say": "Watch this.", "claim": "hype", "at": 14.3},
                     {"say": "Here it comes now.", "claim": "hype", "at": 19.8}])
    return has(problems_of(s), "talks over the payoff")


# --- 5b. The one replay (PR 29) ---

REPLAY = {"from": 20.6, "to": 21.4}


@check
def an_action_short_may_replay_its_impact_once_right_after_the_payoff():
    """an action Short's "replay" (the impact, inside the payoff, holding the hit) is put in right after the payoff
    at half speed: shot 1 ends at the payoff's end, the 0.8 s impact plays for 1.6 s, then the rest of shot 1; the
    payoff still maps to its first showing; the written shots still never share a second"""
    p, problems, _ = cutplan.plan(short(replay=dict(REPLAY)), moment(), secs(short()), 21.0)
    shots = [(s["from"], s["to"], s.get("speed", 1.0)) for s in p["shots"]]
    r = p["replay"]
    return (not problems and shots == [(14.0, 22.5, 1.0), (20.6, 21.4, 0.5), (22.5, 25.0, 1.0), (30.0, 36.0, 1.0)]
            and abs(p["total_s"] - 18.6) < 1e-6 and (r["start"], r["end"]) == (8.5, 10.1)
            and cutplan.to_short(20.0, p["shots"]) == 6.0 and cutplan.to_short(21.0, p["shots"]) == 7.0
            and has(problems_of(short(replay=dict(REPLAY), shots=[{"from": 14.0, "to": 25.0},
                                                                 {"from": 24.0, "to": 31.0}])), "both show"))


@check
def a_replay_is_refused_when_it_breaks_the_owners_terms():
    """refused: in a narrated Short; an impact of 1.5 s or more; outside the payoff; missing the hit (the loudest
    moment); not a {"from", "to"} (a list of replays). An impact over 1 s shows the 1 s round the hit (2 s on screen)"""
    narrated = dict(moment(), type="narrated")
    long_ = problems_of(short(replay={"from": 20.0, "to": 21.8}))
    outside = problems_of(short(replay={"from": 22.0, "to": 23.0}))
    miss = problems_of(short(replay={"from": 21.6, "to": 22.4}), loudest=21.0)
    many = problems_of(short(replay=[REPLAY, REPLAY]))
    nar = cutplan.replay_of(short(replay=dict(REPLAY)), {"from": 20.0, "to": 22.5, "what": "x"}, "narrated")[1]
    trimmed, _ = cutplan.replay_of(short(replay={"from": 20.2, "to": 21.6}), {"from": 20.0, "to": 22.5, "what": "x"},
                                   "action", 21.3)
    return (has(nar, "only an action Short") and has(long_, "only an impact under 1.5 s") and has(outside, "inside the payoff")
            and has(miss, "misses the loudest moment") and has(many, '{"from": s, "to": s}') and narrated
            and (trimmed["from"], trimmed["to"]) == (20.6, 21.6))


@check
def no_voice_over_the_replay_and_its_cuts_need_no_line():
    """a line can't start inside the replay (lines start at their own recording second, which maps to its first
    showing) and one before it ends before the payoff; the cuts into and out of the replay pass as the replay's own"""
    p, problems, _ = cutplan.plan(short(replay=dict(REPLAY)), moment(), secs(short()), 21.0)
    r = p["replay"]
    over = [x for x in p["placed"] if x["start"] < r["end"] and x["end"] > r["start"]]
    reasons = dict(cutplan.cut_reasons(p["shots"], p["placed"], cutplan.events_of(moment(), p["payoff"])))
    return (not problems and not over and reasons.get(8.5) == "the slow-mo replay starts"
            and reasons.get(10.1) == "the slow-mo replay ends")


@check
def a_slowed_shot_splits_at_camera_cuts_on_its_own_timeline():
    """steady.split_points at half speed: a camera cut 0.4 s into the recording falls 0.8 s (24 frames) into the
    replay"""
    return steady.split_points(10.0, 60, [10.4], 0.5) == [24, 36] and steady.split_points(10.0, 60, [10.4]) == [12, 48]


# --- 6. The voice at 1.2 (the owner's pick by ear, Nov 23) and the punchy style ---

@check
def punchy_lines_and_hype():
    """a sentence over 9 words is refused; a hype line ("Wait for it.") needs no evidence, but at most 5 words and
    nothing it names without evidence; no "you" anywhere is a style flag"""
    long = cutplan.style_problems([{"say": "Rush hour on a packed bridge and this SUV is boxed in."}])[0]
    _, flags = cutplan.style_problems([{"say": "The car is fast."}])
    by = {e["id"]: e for e in EVIDENCE}
    ok = truth.line_problems(1, {"say": "Wait for it.", "claim": "hype"}, by, False, FACTS, None, "v4")
    wordy = truth.line_problems(1, {"say": "Wait for it, it is coming now", "claim": "hype"}, by, False, FACTS,
                                None, "v4")
    police = truth.line_problems(1, {"say": "Here come the police.", "claim": "hype"}, by, False, FACTS, None, "v4")
    v3 = truth.line_problems(1, {"say": "Wait for it.", "claim": "hype"}, by, False, FACTS, None, "v3")
    return (has(long, "12 words") and flags and not ok and has(wordy, "at most 5 words") and police and v3)


@check
def the_voice_speed_is_1_20():
    """the owner's speed, picked by ear on Nov 23: engine v4 speaks at 1.20 unless the Short says otherwise, in the
    plan and in assemble.build's own default; explainer warns above 1.4"""
    import inspect
    import assemble
    import explainer
    return (cutplan.SPEED == 1.2 and inspect.signature(assemble.build).parameters["speed"].default == 1.2
            and explainer.MAX_SPEED == 1.4)


@check
def words_a_minute_with_the_pauses_and_voiced_only():
    """speech.wpm: 6 words in 0.0-1.2 s, a 2.8 s pause (a payoff), 6 more in 4.0-5.2 s: 12 words over 5.2 s is 138 a
    minute with the pauses, 300 voiced only; the 0.05 s gaps inside a line count as voice; one line has both the
    same; nothing heard gives None"""
    import speech
    run = [(0.2 * k, 0.2 * k + 0.15, "word") for k in range(5)] + [(1.0, 1.2, "word")]
    two = speech.wpm(run + [(a + 4.0, b + 4.0, w) for a, b, w in run])
    one = speech.wpm(run)
    return (two["words"] == 12 and abs(two["wpm_with_pauses"] - 138.5) < 0.1 and abs(two["wpm_voiced_only"] - 300) < 0.1
            and abs(one["wpm_with_pauses"] - 300) < 0.1 and one["wpm_voiced_only"] == one["wpm_with_pauses"]
            and speech.wpm([])["wpm_with_pauses"] is None and speech.wpm([(0.0, 0.1, "-")])["words"] == 0)


# --- 7 and end to end: a real Short from a made-up recording, through launch.render ---

class FakeChecker:
    def frame(self, jpg, meta):
        return {"dark": False, "empty": False, "back_only": False, "text_bottom": False, "text_right": False,
                "vehicle": True, "crash": True, "police": False, "note": "a test picture"}, None


def fake_voice(record):
    def synth(lines, speed):
        import numpy as np
        record.append(speed)
        out = []
        for n, line in enumerate(lines):
            words = cutplan.words_of(line["say"])
            secs_ = 0.28 * len(words)
            t = np.arange(int(secs_ * 24000)) / 24000
            audio = (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32")
            ws = [{"text": w.upper(), "key": w.lower(), "start": 0.28 * k, "end": 0.28 * k + 0.25, "sentence": n,
                   "pause": False, "colour": None, "tag": None} for k, w in enumerate(words)]
            out.append({"audio": audio, "rate": 24000, "seconds": secs_, "words": ws})
        return out
    return synth


def no_music_build(out_dir, video, shots, lines, spoken, placed, hook, kind, quiet, music, seed, speed, voice, log):
    import assemble
    return assemble.build(out_dir, video, shots, lines, spoken, placed, hook, kind, quiet, None, seed, speed, voice,
                          log)


@functools.cache
def synth_video():
    """The made-up 40 s recording every end-to-end check renders from: a loud hit at 21 s."""
    return make_video(ROOT / "synth.mp4", 40, loud_at=21.0, size="640x360")


def render_short(name, s, heard=None, **parts):
    """One Short from synth_video through launch.render with engine v4: its run folder, its render.json entry, the voice
    speeds asked for and every command it ran. heard: the words the first-word check hears at the start (its voice is a
    tone, so Whisper is stood in for: line 1's own words by default)."""
    v = synth_video()
    tmp = ROOT / name
    tmp.mkdir()
    speeds, commands, run, first = [], [], subprocess.run, launch.first_words_check
    listened = []

    def recorder(args, *a, **k):
        commands.append([str(x) for x in args])
        return run(args, *a, **k)

    def hears(path, say):                       # heard: words, or a function of the file listened to
        listened.append(Path(path).name)
        return (heard(path) if callable(heard) else heard) or say.split()
    subprocess.run = recorder
    launch.first_words_check = lambda path, say, log=print: first(path, say, log, lambda p: hears(p, say))
    try:
        folder = run_folder_with(tmp, [moment(v)], [s])
        data = launch.render(folder, engine="v4", checker=FakeChecker(), indexes=[made_up_idx("synth", v)], facts=FACTS,
                             today=TODAY, shorts_dir=tmp, log=lambda m: None,
                             v4_parts={"synth": fake_voice(speeds), "build": no_music_build, **parts})
    finally:
        subprocess.run, launch.first_words_check = run, first
    return SimpleNamespace(folder=folder, entry=data["shorts"][0], speeds=speeds, commands=commands, name=name,
                           listened=listened)


def ready(made):
    if made.entry["status"] != "READY":
        raise AssertionError("; ".join(made.entry["problems"]))
    made.out = Path(made.entry["folder"])
    made.log = json.loads((made.out / "log.json").read_text(encoding="utf-8"))
    return made


@functools.cache
def plain_render():
    """The made-up action Short, rendered whole once and looked at by every check that needs a finished one."""
    return ready(render_short("plain", short()))


@functools.cache
def replay_render():
    """The same Short with its slow-mo replay (the speech check is plain_render's: here nothing is heard)."""
    heard, launch.speech_check = launch.speech_check, lambda path, log=print, check=None: {"early": [], "later": [], "words": 0}
    try:
        return ready(render_short("replay", short(replay=dict(REPLAY))))
    finally:
        launch.speech_check = heard


@check
@testkit.full
def end_to_end_a_v4_short_names_its_payoff():
    """launch.render with engine v4 on a made-up 40 s recording: READY, 17 s, the payoff named in render.json and on
    the review page, every shot once, the voice at 1.2, no word over a cut or over the payoff, the crash sound up"""
    made = plain_render()
    e, log, speeds = made.entry, made.log, made.speeds
    page = (made.folder / "review.html").read_text(encoding="utf-8")
    cuts = log["cuts_s"]
    pay = e["payoff"]
    words_ok = all(not (w["start_s"] < c < w["end_s"]) for w in log["word_times"] for c in cuts)
    quiet = all(not (pay["at_s"] < w["start_s"] < pay["at_s"] + 2.5) for w in log["word_times"])
    return (abs(e["length_s"] - 17.0) < 0.1 and pay["what"] == "the car slams into the wall"
            and "slams into the wall" in page and speeds == [1.2] and words_ok and quiet
            and e["shown"] == [["synth", 14.0, 25.0], ["synth", 30.0, 36.0]]
            and (log["game_audio"].get("quiet_lift_db") is not None))


@check
@testkit.full
def end_to_end_a_v4_short_with_its_replay():
    """launch.render engine v4 with "replay": READY, 18.6 s, the replay shot made at half speed (48 frames for 0.8 s
    of the recording) right after the payoff, its sound slowed, the crash sound up over the payoff and the replay, no
    word over either, the replay in render.json and as a flag that says no ring was drawn, and the used footage
    counted once"""
    whole = replay_render()
    e, log = whole.entry, whole.log
    play = next(s for s in log["shots"] if s.get("replay"))
    made = steady.count_frames(play["clip"]) if Path(play["clip"]).is_file() else None
    r = e["replay"]
    silent = all(not (w["start_s"] < r["end"] and w["end_s"] > e["payoff"]["at_s"]) for w in log["word_times"])
    return (abs(e["length_s"] - 18.6) < 0.1 and play["frames"] == 48 and play["speed"] == 0.5
            and (made in (None, 48)) and (r["start"], r["end"]) == (8.5, 10.1) and silent
            and any("No highlight ring" in f for f in e["flags"])
            and e["shown"] == [["synth", 14.0, 22.5], ["synth", 22.5, 25.0], ["synth", 30.0, 36.0]]
            and log["game_audio"].get("quiet_lift_db") is not None)


@check
def a_short_without_a_payoff_is_rejected_on_the_page():
    """the interest gate end to end: no payoff, FAILED with the reason (nothing rendered)"""
    with tempfile.TemporaryDirectory() as tmp:
        v = Path(tmp) / "synth.mp4"
        v.write_bytes(b"x")
        pick = moment(v)
        folder = run_folder_with(tmp, [pick], [short(payoff=None)])
        data = launch.render(folder, engine="v4", checker=FakeChecker(), indexes=[made_up_idx("synth", v)],
                             facts=FACTS, today=TODAY, shorts_dir=Path(tmp), log=lambda m: None,
                             v4_parts={"synth": fake_voice([]), "loudest": lambda *a: 21.0})
        e = data["shorts"][0]
        return e["status"] == "FAILED" and has(e["problems"], "No clear payoff, no Short")


def made_again(name, early=(), heard=None):
    """The made-up Short again without rendering it (the shots and the finished Short are plain_render's, copied in), with
    what the speech check hears (early: the swearing) and the words heard at the start: its render.json entry."""
    first = plain_render()

    def make_shot(video, s, path, grade, log):
        shutil.copy(next(x["clip"] for x in first.log["shots"] if x["n"] == s["n"]), path)
        return {"pieces": []}

    def build(out_dir, *rest):                 # what assemble.build leaves: the Short, its captions, voice and log
        for name in ("short.mp4", "captions.ass", "voice.wav"):
            shutil.copy(first.out / name, out_dir)
        there, here = (json.dumps(str(x))[1:-1] for x in (first.out, out_dir))
        text = (first.out / "log.json").read_text(encoding="utf-8").replace(there, here)
        (Path(out_dir) / "log.json").write_text(text, encoding="utf-8")
        return json.loads(text)
    old = launch.speech_check
    launch.speech_check = lambda path, log=print, check=None: {"early": list(early), "later": [], "words": 9}
    try:
        return render_short(name, short(), heard, make_shot=make_shot, build=build).entry
    finally:
        launch.speech_check = old


@check
@testkit.full
def early_swearing_fails_with_the_shorts_metadata():
    """PR 30: the speech check's "swearing in the first 7 s" was raised outside render_one's try, so render() replaced
    the Short's entry with a bare stub (no moment, folder, hooks or type on the review page). Now it is FAILED with
    all of them and the reason (the shots and the finished Short are plain_render's, copied in)"""
    e = made_again("swearing", early=[(1.2, "damn")])
    return (e["status"] == "FAILED" and has(e["problems"], "spoken swearing in the first 7 s")
            and e.get("moment") == "M1" and e.get("type") == "action" and e.get("folder")
            and e.get("hooks") == short()["hooks"])


@check
def the_first_three_words_heard_must_be_the_hooks():
    """PR 34: Kokoro's "Can you ..." is heard as "And you ..." (M24 went up like that), so the render transcribes the first
    2 s and compares the first 3 words with line 1's: "and you survive" for "Can you survive" is refused and both are
    named; capitals, punctuation and "six" for 6 are no difference; a 2-word line needs its 2 words; nothing heard is
    refused; where it can't be checked (no Whisper, or it breaks) the Short gets a flag, never a silent pass"""
    import speech
    say = "Can you survive this GTA 6 fall?"
    bad = launch.first_words_check("x.mp4", say, listen=lambda p: ["And", "you", "survive", "this"])
    good = launch.first_words_check("x.mp4", say, listen=lambda p: ["CAN", "you,", "survive...", "that"])
    same = [speech.first_words_problem(*x) for x in (("Will GTA 6 animals attack?", ["will", "GTA", "six", "animals"]),
                                                     ("Don’t move.", ["Dont", "move."]),
                                                     ("Watch this.", ["Watch", "this,", "wait"]))]
    wrong = [speech.first_words_problem(*x) for x in ((say, []), (say, ["Can", "you"]), ("Watch this.", ["Watch"]))]

    def broken(path):
        raise RuntimeError("no model")
    none, flag = launch.first_words_check("x.mp4", say, log=lambda m: None, listen=broken)
    return ("can you survive" in bad[0] and "and you survive" in bad[0] and bad[1] is None and good == (None, None)
            and same == [None] * 3 and all(wrong) and none is None and "not checked" in flag)


@check
def only_the_first_two_seconds_are_transcribed_for_it():
    """speech.first_words hands Whisper the first 2 s of sound and nothing else; the swear check still hears all of it"""
    import numpy as np
    import speech
    sent, saved = [], (speech.audio, speech.load)

    def transcribe(sound, **kw):
        sent.append(len(sound))
        return [SimpleNamespace(words=[SimpleNamespace(start=0.0, end=0.3, word=" Can")])], None
    speech.audio = lambda p: np.zeros(16000 * 10, np.float32)
    speech.load = lambda: SimpleNamespace(transcribe=transcribe)
    try:
        heard, all_of_it = speech.first_words("x.mp4"), speech.words("x.mp4")
    finally:
        speech.audio, speech.load = saved
    return heard == ["Can"] and all_of_it == [(0.0, 0.3, "Can")] and sent == [32000, 160000]


@check
@testkit.full
def a_short_whose_hook_is_misheard_fails_and_one_heard_right_is_ready():
    """PR 34, through launch.render: line 1 is "Watch closely." (it was "Watch this.", filler since Nov 26); heard as
    "Wash closely" the Short is FAILED with both named and keeps its metadata; heard right it is READY (the shots and
    the finished Short are plain_render's, copied in)"""
    bad, good = made_again("misheard", heard=["Wash", "closely."]), made_again("heard", heard=["watch", "Closely"])
    return (bad["status"] == "FAILED" and has(bad["problems"], "watch closely") and has(bad["problems"], "wash closely")
            and bad.get("moment") == "M1" and bad.get("folder") and good["status"] == "READY")


def voice_folder(tmp, lines=((0.0, 1.0), (1.5, 2.5)), rate=48000):
    """A Short's folder as assemble.build leaves it, as far as the first-word check reads it: voice.wav (16-bit stereo,
    a tone where each line is, its first 0.02 s quiet as Kokoro's trimmed lead) and log.json with the lines' times."""
    import wave
    import numpy as np
    total = lines[-1][1] + 0.5
    v = np.zeros(int(total * rate), np.float32)
    for k, (a, b) in enumerate(lines):
        n0, n1 = int((a + 0.02) * rate), int(b * rate)
        v[n0:n1] = 0.3 * np.sin(2 * np.pi * (220 + 110 * k) * np.arange(n1 - n0) / rate)
    with wave.open(str(Path(tmp) / "voice.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.repeat((v * 32767).astype("<i2"), 2).tobytes())
    log = {"durations": {"lines": [{"line": k, "start_s": a, "end_s": b} for k, (a, b) in enumerate(lines, 1)]}}
    (Path(tmp) / "log.json").write_text(json.dumps(log), encoding="utf-8")
    return v


@check
def the_first_word_check_hears_the_clean_voice_up_to_the_end_of_line_1():
    """rule 6 (Nov 26: on the mixed Short, Whisper misheard "Can", and "steal" became "take"): launch.hook_voice cuts
    voice.wav, the voice alone (no music, game sound or whoosh), from line 1's start to its end, with 0.06 s of silence
    in front, 0.08 s before the first word with the line's own trimmed lead; nothing of line 2 is in it, and every sample
    of line 1 is; a folder with no voice.wav or log.json gives None (the Short is then flagged, never passed)"""
    import wave
    import numpy as np
    with tempfile.TemporaryDirectory() as tmp:
        v = voice_folder(tmp)
        clip = launch.hook_voice(tmp)
        with wave.open(str(clip)) as w:
            rate, ch = w.getframerate(), w.getnchannels()
            got = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, ch)[:, 0].astype(np.float32) / 32767
        lead = int(launch.HOOK_LEAD_S * rate)
        quiet = np.abs(got[:lead + int(0.02 * rate)]).max()
        same = np.abs(got[lead:] - v[:len(got) - lead]).max()
        (Path(tmp) / "voice.wav").unlink()
        gone = launch.hook_voice(tmp)
    import assemble
    import speech
    advice = speech.first_words_problem("Can anyone steal this?", ["Can", "anyone", "still"])
    print(f"       {len(got) / rate:.3f} s, silent first {(lead / rate + 0.02):.2f} s (peak {quiet:.4f}), off by {same:.5f}; "
          f"{advice}")
    return (clip.name == "first_words.wav" and abs(len(got) / rate - (launch.HOOK_LEAD_S + 1.0)) < 1e-3 and quiet == 0
            and same < 1e-3 and abs(launch.HOOK_LEAD_S + assemble.LEAD_KEEP_S - 0.08) < 1e-9 and gone is None
            and "is heard right" not in advice and "can anyone steal" in advice and "can anyone still" in advice)


@check
@testkit.full
def the_render_fails_on_the_clean_voice_and_only_flags_the_mix():
    """through launch.render: the first-word check hears first_words.wav first, the clean hook. Heard right there and
    wrong in the mix (music or a whoosh over "Can"), the Short is READY with a FIRST WORDS IN THE MIX flag; heard wrong
    on the clean voice it is FAILED, whatever the mix says; the message no longer says "Can anyone ..." is heard right
    (in the real mix it wasn't, Nov 25)"""
    def mix_only(path):
        return ["Wash", "closely."] if Path(path).name == "short.mp4" else ["watch", "closely"]

    def clean_only(path):
        return ["Wash", "closely."] if Path(path).name == "first_words.wav" else ["watch", "closely"]
    ready, failed = made_again("mixheard", heard=mix_only), made_again("cleanheard", heard=clean_only)
    return (ready["status"] == "READY" and any(f.startswith("FIRST WORDS IN THE MIX") for f in ready["flags"])
            and failed["status"] == "FAILED" and has(failed["problems"], "wash closely")
            and not has(failed["problems"], "Can anyone"))


@check
def filler_lines_are_refused():
    """PR 34, the owner's rule: a line that only fills time ("Wait for it.", "That was close!", "Ouch", "Let's see") is
    refused, alone or next to other words, in any case; a line that only looks like one is fine"""
    def found(say):
        return has(truth.story_problems(short(lines=[{"say": say, "claim": "hype", "at": 14.0}]), moment(), FACTS, "v4"),
                   "is filler")
    said = ["Wait for it.", "WAIT... for it!", "That was close.", "On the helmet. Ouch.", "Let’s see what happens.",
            "So let's see.", "Okay, wait for it"]
    fine = ["Nowhere to go now.", "The gap was closer than it looked.", "You wait for the light.", "Vouch for this one.",
            "Let's go."]
    return all(found(x) for x in said) and not any(found(x) for x in fine)


@check
def render_and_render_one_default_to_engine_v4_like_the_command_line():
    """PR 30: main() renders with v4 by default, but render() and render_one() defaulted to v3, so a caller that
    left the engine out got the old engine"""
    import argparse
    import inspect
    seen = {}
    real = argparse.ArgumentParser.parse_args
    old_render = launch.render
    try:
        argparse.ArgumentParser.parse_args = lambda self, *a, **k: real(self, ["render"])
        launch.render = lambda *a, **k: seen.update(k) or {"shorts": []}
        launch.main()
    finally:
        argparse.ArgumentParser.parse_args = real
        launch.render = old_render
    return (seen.get("engine") == "v4"
            and inspect.signature(launch.render).parameters["engine"].default == "v4"
            and inspect.signature(launch.render_one).parameters["engine"].default == "v4")


# --- 8. The first seconds (PR 30) ---

@check
def the_first_word_by_0_2_s_the_hook_by_3_5_s_line_2_by_4_s():
    """the swipe is decided in 2-3 s: line 1 0.3 s in is refused (first word by 0.2 s), a hook still talking at 3.9 s
    is refused, line 2 at 4.5 s is refused; the fixture (line 1 at 0.0, ends 0.56; line 2 at 3.5) passes"""
    late = short(lines=[dict(short()["lines"][0], at=14.3)] + short()["lines"][1:])
    long_hook = short(lines=[{"say": "Watch this car go. It is fast. It is red. Look at it now.", "claim": "hype",
                       "at": 14.0}]
                      + short()["lines"][1:])
    slow_two = short(lines=short()["lines"][:1] + [dict(short()["lines"][1], at=18.5)] + short()["lines"][2:])
    p, problems, _ = cutplan.plan(short(), moment(), secs(short()), 21.0)
    return (has(problems_of(late), "line 1 starts 0.30 s in") and has(problems_of(long_hook), "ends 3.9 s in")
            and has(problems_of(slow_two), "line 2 starts 4.5 s in") and not problems
            and p["placed"][0]["start"] == 0.0 and p["placed"][1]["start"] <= cutplan.SECOND_LINE_S)


class LeadKokoro:
    """Kokoro's place in explainer.speak_sentence: 0.25 s of near silence, then a tone, as the model starts a line."""


def fake_sentence(kokoro, sentence, voice, speed):
    import numpy as np
    rate = 24000
    lead, body = np.full(int(0.25 * rate), 1e-5, np.float32), 0.3 * np.sin(np.arange(int(0.6 * rate)) / 10.0)
    words = sentence.rstrip(".!?").split()
    step = 0.6 / len(words)
    times = [(0.25 + k * step, 0.25 + (k + 1) * step) for k in range(len(words))]
    return np.concatenate([lead, body.astype(np.float32)]), rate, words, times, True


@check
def kokoros_lead_in_is_trimmed_so_the_line_starts_on_its_first_word():
    """assemble.speak cuts each sentence's quiet lead-in (0.25 s in the stand-in) to 0.02 s and moves the word times
    with it: the first word starts at 0.02 s, not 0.25 s, and the line is 0.23 s shorter"""
    import assemble
    import explainer
    old = explainer.speak_sentence
    explainer.speak_sentence = fake_sentence
    try:
        out = assemble.speak(LeadKokoro(), [{"say": "Watch this car."}], "am_puck", 1.2)
    finally:
        explainer.speak_sentence = old
    w = out[0]["words"]
    return abs(w[0]["start"] - 0.02) < 0.011 and abs(out[0]["seconds"] - 0.62) < 0.011 and w[1]["start"] > w[0]["start"]


SPOIL = ["This GTA 6 motorbike ride ends BADLY", "He crashes at 200 mph", "The bike explodes", "He dies here",
         "He gets busted in GTA 6", "This rider gets wiped out", "The jump fails", "He survives the fall",
         "He doesn't make it", "This SUV blows up", "He got away clean"]
CLEAN = ["Watch the black SUV in GTA 6", "Can you survive jumping off this bridge in GTA 6?", "Watch this crash",
         "Does this jump end badly?", "Watch the bike on the left", "Wait for the cops"]


@check
def a_hook_title_or_open_card_that_tells_the_result_is_refused():
    """the flop's hook ("ends BADLY") gave the result away and lost 59 %: result words are refused in any hook, the
    title and the open card; a question may name one; "crash" as a thing to watch is not a result"""
    bad = [t for t in SPOIL if not cutplan.spoilers_in(t)]
    good = [t for t in CLEAN if cutplan.spoilers_in(t)]
    hook = problems_of(short(hooks=["Watch this crash", "This GTA 6 motorbike ride ends BADLY", "x"]))
    title = problems_of(short(title="The black SUV crashes in GTA 6"))
    card = problems_of(short(open_card="He dies"))
    return (not bad and not good and has(hook, "hook 2: \"This GTA 6 motorbike ride ends BADLY\" tells the result")
            and has(title, "title:") and has(card, "open card:") and not problems_of(short()))


def suv_moment(at=13.0, to=14.5, extra=()):
    m = moment()
    m["evidence"] = m["evidence"] + [{"id": "E3", "kind": "seen", "value": "A black SUV waits at the lights.",
                                      "from_s": at, "to_s": to, "source": "made up"}, *extra]
    return m


@check
def the_hooks_subject_is_in_the_evidence_at_frame_0():
    """"Watch the black SUV" needs the SUV in the moment's evidence at shot 1's start (13.0-14.5 covers 14.0); seen
    only later it is refused; "the cops" with the police not seen by the vision check is refused; a hook that names
    no subject ("Watch this crash") needs nothing"""
    hooks = ["Watch the black SUV in GTA 6", "Wait for the cops", "Watch this crash"]
    ok = problems_of(short(hooks=hooks, star=0), m=suv_moment())
    later = problems_of(short(hooks=hooks, star=0), m=suv_moment(18.0, 24.0))
    cops = problems_of(short(hooks=hooks, star=1), m=suv_moment(extra=[{
        "id": "E4", "kind": "seen", "value": "Police cars block the road.", "from_s": 12.0, "to_s": 18.0,
        "not_seen": ["police"], "source": "made up"}]))
    none = problems_of(short(hooks=hooks, star=2))
    return (not has(ok, "no evidence shows it") and has(later, "the hook names 'suv'") and has(cops, "names 'police'")
            and not has(none, "no evidence shows it"))


@check
def a_moved_first_frame_is_checked_again_for_the_hooks_subject():
    """steady.clear_start moves shot 1 from 14.0 to 15.0 s (a blurred frame): line 1 moves with it to the new frame 0,
    and the SUV, seen only up to 14.5 s, is no longer at frame 0, so the Short is refused before anything is made"""
    with tempfile.TemporaryDirectory() as tmp:
        v = Path(tmp) / "synth.mp4"
        v.write_bytes(b"x")
        pick = dict(suv_moment(), path=str(v))
        hooks = ["Watch the black SUV", "Watch this car go", "Watch this crash"]
        folder = run_folder_with(tmp, [pick], [short(hooks=hooks, star=0)])
        moved = lambda video, a, latest, focus: (a + 1.0, [], [{"at": a, "why": ["it is blurry"]}])
        data = launch.render(folder, engine="v4", checker=FakeChecker(), indexes=[made_up_idx("synth", v)],
                             facts=FACTS, today=TODAY, shorts_dir=Path(tmp), log=lambda m: None,
                             v4_parts={"synth": fake_voice([]), "loudest": lambda *a: 21.0, "clear": moved})
        e = data["shorts"][0]
        return (e["status"] == "FAILED" and has(e["problems"], "no evidence shows it at shot 1's start (15.00 s)")
                and not has(e["problems"], "in none of the shots"))


@check
def the_open_card_is_5_words_at_most_small_and_above_the_action():
    """the open card: at most 5 words ("Watch the black SUV in GTA 6" makes "Watch the black SUV"; a 7-word card is
    refused), at most 120 px, its text centred at y 487 of 1920 and fitted into y 390-584 (the top of the safe area,
    over a HUD close-up's top at 600), for 2 s from frame 0, in lines of up to 20 characters ("Watch the overpass
    railing" takes 2 lines, where explainer's 13 made 3 and a smaller font)"""
    import re
    import assemble
    derived = cutplan.card_words(short(hooks=["Watch the black SUV in GTA 6", "b", "c"], star=0))
    long_ = problems_of(short(open_card="Watch this black SUV take the bridge"))
    ev = assemble.card_events("WATCH THE|BLACK SUV")
    pos = [tuple(map(float, x)) for e in ev for x in re.findall(r"\\pos\((\d+),(\d+)\)", e)]
    size = [int(x) for e in ev for x in re.findall(r"\\fs(\d+)", e)]
    lines = launch.open_card_text("Watch the overpass railing", assemble.CARD_CHARS)
    return (derived == "Watch the black SUV" and has(long_, "has 7 words; at most 5") and pos
            and lines == "WATCH THE OVERPASS|RAILING"
            and all(380 <= y <= 655 for _, y in pos) and size and max(size) <= assemble.CARD_SIZE
            and assemble.CARD_Y + assemble.CARD_ROOM / 2 <= 600)


# --- 12. Hooks, the open card and the title (the owner's rules of Nov 26) ---

@check
def a_trailing_in_gta_6_comes_off_the_card_before_a_question_mark_too():
    """rule 7: the open card leaves out a trailing "in GTA 6" before "?" as it does before "." or "!" ("Can you survive
    this bridge in GTA 6?" made a 7-word card, so it was refused); "GTA 6" in the middle stays, and so does a card given
    by hand"""
    def card(h):
        return cutplan.card_words(short(hooks=[h, "b", "c"], star=0))
    return (card("Can you survive this bridge in GTA 6?") == "Can you survive this bridge?"
            and card("Can you STEAL this in GTA VI?!") == "Can you STEAL this?!"
            and card("Watch the black SUV in GTA 6") == "Watch the black SUV"
            and card("Which GTA 6 animal takes you DOWN first?") == "Which GTA 6 animal takes you DOWN first?"
            and cutplan.card_words(short(open_card="Steal it in GTA 6?")) == "Steal it in GTA 6?"
            and not has(problems_of(short(hooks=["Can you survive this bridge in GTA 6?", "b", "c"], star=0)), "7 words"))


@check
def filler_hooks_are_refused_and_named_hooks_are_not():
    """rule 2: no filler in a hook, the open card, the title or a line: "Watch this.", "Watch this!", "Watch this, you're
    flying through traffic." and "Wait for it" (the 1.2K bike Short opened "Watch this. Wait for it.") are refused
    wherever they are; "Watch this crash", "Watch the black SUV", "Watch this car go" and "Wait for the cops" name
    something to watch and pass"""
    def hit(**over):
        return has(problems_of(short(**over)), "is filler")

    def line(say):
        return has(truth.story_problems(short(lines=[{"say": say, "claim": "hype", "at": 14.0}]), moment(), FACTS, "v4"),
                   "is filler")
    bad = [hit(hooks=["Watch this. Wait for it.", "b", "c"]), hit(hooks=["Watch this crash", "Watch this!", "c"]),
           hit(open_card="Wait for it"), hit(title="WATCH this, GTA 6 traffic is wild"), line("Watch this."),
           line("Watch this, you're flying through traffic."), line("Okay, watch this")]
    good = [hit(hooks=["Watch this crash", "Watch the black SUV", "Watch this car go"]),
            hit(hooks=["Wait for the cops", "b", "c"]), line("Watch this car go."), line("Watch the black SUV.")]
    print(f"       refused {bad}; passed {[not g for g in good]}")
    return all(bad) and not any(good)


@check
def a_question_hook_needs_its_stakes_on_screen_at_frame_0():
    """rule 2: a hook is a statement or a command; a question only when the stakes are readable at frame 0. The starred
    hook asking with no "stakes_at_0" is refused, and so is line 1 asking; "stakes_at_0" passes when its evidence covers
    shot 1's start (E1, driving, 10-40 s) and it says why in 3+ words; E2 (the crash, 20-23 s), an id the moment hasn't,
    or a one-word why are refused; a statement needs nothing"""
    q = dict(hooks=["Can you stop this car?", "b", "c"], star=0)
    none = problems_of(short(**q))
    ok = problems_of(short(**q, stakes_at_0={"evidence": ["E1"], "why": "the car is already racing down the road"}))
    late = problems_of(short(**q, stakes_at_0={"evidence": ["E2"], "why": "the crash comes soon"}))
    unknown = problems_of(short(**q, stakes_at_0={"evidence": ["E9"], "why": "the car is racing"}))
    terse = problems_of(short(**q, stakes_at_0={"evidence": ["E1"], "why": "car"}))
    spoken = problems_of(short(lines=[dict(short()["lines"][0], say="Watch it go?")] + short()["lines"][1:]))
    word = "is a question"
    return (has(none, word) and not has(ok, word) and has(late, word) and has(unknown, word) and has(terse, word)
            and has(spoken, word) and not has(problems_of(short()), word))


@check
def the_spoken_hook_the_card_and_the_title_share_the_key_verb():
    """rules 2 and 8 (the airliner Short: its title said STEAL, its voice "take", 1.2K views): "key_verb" is said by line
    1, the open card and the title, in any form (steal, stealing); where it isn't, that place is named; no key_verb is
    refused; the fixture's (watch) passes"""
    air = dict(key_verb="steal", title="Can you STEAL this GTA 6 airliner?", hooks=["Steal this airliner", "b", "c"],
               star=0)

    def with_line(say, **over):
        return problems_of(short(**dict(air, **over), lines=[dict(short()["lines"][0], say=say)] + short()["lines"][1:]))
    took = with_line("Take this airliner.")
    said = with_line("Stealing an airliner.")
    card = with_line("Steal this airliner.", open_card="Take the airliner")
    title = with_line("Steal this airliner.", title="Can you TAKE this GTA 6 airliner?")
    none = problems_of(short(key_verb=None))
    k = "the key verb"
    return (has(took, "line 1 (the spoken hook) doesn't say the key verb \"steal\"") and not has(said, k)
            and has(card, "the open card doesn't say the key verb") and not has(card, "line 1 (the spoken hook)")
            and has(title, "the title doesn't say the key verb") and has(none, "key_verb:")
            and not has(problems_of(short()), k) and not has(problems_of(short()), "key_verb"))


@check
def the_short_ends_as_the_talking_ends_or_on_the_replay():
    """no dead air at the end: the last word 4.6 s before the end is refused; the fixture's last word ends 0.08 s
    before; an ending on the slow-mo replay is allowed however long after the last word"""
    early = short(lines=short()["lines"][:3] + [dict(short()["lines"][3], at=30.3)])
    shots = [{"n": 1, "from": 0.0, "to": 6.0, "start": 0.0, "end": 6.0},
             {"n": 2, "from": 5.0, "to": 5.8, "start": 6.0, "end": 7.6, "speed": 0.5, "replay": True}]
    placed = [{"line": 1, "start": 0.0, "end": 1.0}]
    return (has(problems_of(early), "goes on 4.6 s after the last word") and not problems_of(short())
            and not cutplan.ending_problems(shots, placed)
            and cutplan.ending_problems(shots[:1], placed))


@check
@testkit.full
def no_subscribe_pill_and_no_click_in_a_v4_short():
    """the v4 Short has no "@NeonGetaway • Subscribe" pill in its captions and no click in its sound effects (only the
    cuts' whooshes), and its first word is at 0.0 s"""
    made = plain_render()
    ass = (made.out / "captions.ass").read_text(encoding="utf-8")
    log = made.log
    last = max(w["end_s"] for w in log["word_times"])
    return ("Subscribe" not in ass and "NeonGetaway" not in ass
            and all(x["kind"] == "whoosh" for x in log["sfx"]) and log["word_times"][0]["start_s"] <= 0.2
            and log["durations"]["video_s"] - last <= cutplan.END_AFTER_WORDS_S + 0.05)


# --- 9. The caption check sees a v4 Short's captions (PR 31: M24's check list was empty and nothing said so) ---

def v4_captions(tmp, s=None, checker=None, burn=None, late=0.0):
    """A v4 Short's captions without a render: the plan, then assemble's own word times, visuals and burnt-in captions,
    and what launch.render_v4 makes of them: (caption_checks, report, bad, flags). burn(words) may change the words that
    are burnt in; late moves the last word that many seconds."""
    import assemble
    import explainer as ex
    tmp, s = Path(tmp), s or short()
    p, _, _ = cutplan.plan(s, moment(), secs(s), 21.0)
    for x in p["shots"]:
        x["clip"] = str(tmp / f"shot{x['n']}.mp4")
    _, _, words = assemble.voice_track(fake_voice([])(s["lines"], 1.2), p["placed"], p["total_s"])
    words[-1].update(start=words[-1]["start"] + late, end=words[-1]["end"] + late)
    events = ex.word_events(burn([dict(w) for w in words]) if burn else words, p["total_s"])
    (tmp / "captions.ass").write_text(ex.ass_document(events), encoding="utf-8")
    run_log = {"word_times": [{"text": w["text"], "start_s": w["start"], "end_s": w["end"]} for w in words],
               "visuals": assemble.visuals(p["shots"], p["placed"], p["total_s"])}
    plan = [[{"clip": x["clip"], "video": "synth", "from_s": x["from"], "inset": None}] for x in p["shots"]]
    real = launch.frame_jpeg
    launch.frame_jpeg = lambda *a, **k: tmp / "frame.jpg"       # topictest makes real ones; here only the answer counts
    try:
        checks = launch.caption_checks(run_log, plan, s["lines"], checker or FakeChecker(), {}, tmp)
    finally:
        launch.frame_jpeg = real
    return (checks, *launch.caption_coverage(run_log, plan, s["lines"], tmp / "captions.ass", checks))


class NoCrash(FakeChecker):
    """The vision model, seeing no crash in any frame."""
    def frame(self, jpg, meta):
        answer, error = super().frame(jpg, meta)
        return dict(answer, crash=False), error


@check
def the_caption_check_sees_every_caption_of_a_v4_short():
    """the fixture's 4 lines are 13 words: all 13 are found burnt in and over a shot, and the one the check knows
    ("CRASH", line 3) is asked about its own frame at its own second of the recording; nothing is flagged"""
    with tempfile.TemporaryDirectory() as tmp:
        checks, report, bad, flags = v4_captions(tmp)
    got = [(c["word"], c["line"], c["rule"], c["ok"], c["rec_s"]) for c in checks]
    return (report == {"shown": 13, "over_a_shot": 13, "tested": 1, "unknown_things": []} and not bad and not flags
            and got == [("CRASH", 3, "crash in the frame", True, 23.43)])


@check
def a_caption_on_the_seam_of_two_shots_is_over_a_shot():
    """the log rounds its times to 1 ms, so two visuals can leave a gap of 1 ms between them: a caption that starts in it
    is not "over none of the Short's shots"; one 0.1 s past the end is"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "captions.ass").write_text("Dialogue: 2,0:00:01.00,0:00:02.00,Caption,,0,0,0,,{}HELLO", encoding="utf-8")
        plan = [[{"clip": str(tmp / "a.mp4")}], [{"clip": str(tmp / "b.mp4")}]]
        visuals = [{"line": 1, "source": str(tmp / "a.mp4"), "start_s": 0.0, "seconds": 0.999},
                   {"line": 1, "source": str(tmp / "b.mp4"), "start_s": 1.0, "seconds": 1.0}]

        def bad(t):
            log = {"word_times": [{"text": "HELLO", "start_s": t, "end_s": t + 0.2}], "visuals": visuals}
            return launch.caption_coverage(log, plan, [{"say": "Hello."}], tmp / "captions.ass", [])[1]
        return bad(0.9995) == [] and bad(1.5) == [] and len(bad(2.1)) == 1


@check
def a_bad_caption_in_a_v4_short_is_caught():
    """deliberately bad captions: "CRASH" over a frame with no crash fails; a burnt-in word the voice doesn't say, a
    word with no caption, no captions at all and a caption past the last shot each fail, naming the word"""
    def swap(words):
        words[6]["text"] = "BRIDGE"                  # "That crash was huge": CRASH is burnt in as BRIDGE
        return words
    with tempfile.TemporaryDirectory() as tmp:
        blind = v4_captions(tmp, checker=NoCrash())[0]
        wrong = v4_captions(tmp, burn=swap)[2]
        missing = v4_captions(tmp, burn=lambda words: words[:3] + words[4:])[2]
        nothing = v4_captions(tmp, burn=lambda words: [])[2]
        past = v4_captions(tmp, late=60.0)[2]

    def why(rows):
        return [r["why"] for r in rows if not r["ok"]]
    return (len(why(blind)) == 1 and "sees no crash" in why(blind)[0]
            and why(wrong) == ["the caption is 'bridge' where the line says 'crash'"]
            and why(missing) == ["the caption is 'it' where the line says 'for'"]
            and why(nothing) == ["the line's word 'watch' has no caption"]
            and why(past) == ["the line's word 'going' has no caption", "the caption shows over none of the Short's shots"])


@check
def captions_it_cannot_test_are_flagged_never_silent():
    """M24's own lines (23 words, none of them one the check knows): the report says 23 shown, 0 tested; a flag says
    none was checked against its frame and names OVERPASS; the review page shows the count (it showed nothing)"""
    m24 = short(lines=[{"say": "Can you survive jumping off this overpass?", "claim": "ask", "at": 14.0},
                       {"say": "Wait for it.", "claim": "hype", "at": 17.5},
                       {"say": "Yes, you get right back up.", "claim": "on_screen", "evidence": ["E2"], "at": 23.0},
                       {"say": "You just run off like nothing happened.", "claim": "on_screen", "evidence": ["E2"],
                        "at": 33.9}])
    with tempfile.TemporaryDirectory() as tmp:
        checks, report, bad, flags = v4_captions(tmp, m24)
    page = launch.checks_block({"caption_checks": checks, "captions": report})
    return (checks == [] and not bad
            and report == {"shown": 23, "over_a_shot": 23, "tested": 0, "unknown_things": ["OVERPASS"]}
            and len(flags) == 1 and "none of the 23 words" in flags[0] and "OVERPASS names a thing" in flags[0]
            and "23 words shown" in page and "<b>0</b> checked" in page)


CARD_LINES = [{"say": "Can you survive this overpass jump or the overpass?"},       # 0.28 s a word: the last one at 2.24 s
              {"say": "That overpass jump hurt."}]
OPEN_CARD = "Survive this|overpass jump?"


def built_captions(tmp):
    """captions.ass as assemble.build writes it for CARD_LINES under OPEN_CARD (it is stopped right after), and what
    the caption check needs to judge it: (ass, judge(ass) -> (report, the whys of its bad rows))."""
    import assemble
    import explainer as ex
    tmp = Path(tmp)
    spoken = fake_voice([])(CARD_LINES, 1.2)
    placed = [{"line": 1, "start": 0.0, "end": 2.52}, {"line": 2, "start": 4.0, "end": 5.12}]
    shots = [{"n": 1, "from": 0.0, "to": 8.0, "start": 0.0, "end": 8.0, "frames": 240, "clip": str(tmp / "shot1.mp4")}]

    class Written(Exception):
        pass

    def stop(*a):
        raise Written
    real, ex.pick_music = ex.pick_music, stop
    try:
        assemble.build(tmp, "synth.mp4", shots, CARD_LINES, spoken, placed, OPEN_CARD, "action")
    except Written:
        pass
    finally:
        ex.pick_music = real
    _, _, words = assemble.voice_track(spoken, placed, 8.0)
    run_log = {"word_times": [{"text": w["text"], "start_s": w["start"], "end_s": w["end"]} for w in words],
               "visuals": assemble.visuals(shots, placed, 8.0)}

    def judge(ass):
        (tmp / "captions.ass").write_text(ass, encoding="utf-8")
        report, bad, _ = launch.caption_coverage(run_log, [[{"clip": shots[0]["clip"]}]], CARD_LINES,
                                                 tmp / "captions.ass", [])
        return report, [r["why"] for r in bad]
    return (tmp / "captions.ass").read_text(encoding="utf-8"), judge


def captions_in(ass):
    """[(start, end, word)] of the captions in an .ass file: each word with the times of the caption it is in (one word
    a caption until Nov 26, 2-3 since)."""
    def s(t):
        h, m, x = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(x)
    return [(s(f[1]), s(f[2]), w) for line in ass.splitlines() if ",Caption," in line
            for f in [line.split(",", 9)] if line.startswith("Dialogue")
            for w in re.sub(r"\{[^}]*\}", " ", f[9]).split()]


def caption_lines_in(ass):
    """[(start, end, text)] of each caption event (the words it shows, tags left out)."""
    out = []
    for line in ass.splitlines():
        words = captions_in(line)
        if words:
            out.append((words[0][0], words[0][1], " ".join(w for _, _, w in words)))
    return out


def edited_caption(line, word, new=None):
    """One caption event of captions.ass with `word` taken out (new=None) or put in its place; None when no word is
    left in it."""
    head, text = line.split(",", 9)[:9], line.split(",", 9)[9]
    pieces, done = re.split(r"(\{[^}]*\})", text), False
    for k, piece in enumerate(pieces):
        if piece.startswith("{") or done:
            continue
        ws = piece.split(" ")
        if word in ws:
            i = ws.index(word)
            ws = ws[:i] + ws[i + 1:] if new is None else ws[:i] + [new] + ws[i + 1:]
            pieces[k], done = " ".join(ws), True
    joined = "".join(pieces)
    if not re.sub(r"\{[^}]*\}", " ", joined).split():
        return None
    return ",".join(head) + "," + joined


@check
def no_caption_repeats_a_word_the_open_card_shows():
    """PR 33 (the overpass Short said "...OVERPASS?" on the card and in the caption at 0:01): while the open card is up, a
    caption shows only the words it doesn't: "Can you survive this overpass jump or the overpass?" under SURVIVE THIS /
    OVERPASS JUMP? captions CAN YOU OR THE, each for its own time; the same words after the card are captioned as always"""
    with tempfile.TemporaryDirectory() as tmp:
        ass, judge = built_captions(tmp)
        report, why = judge(ass)
    caps = captions_in(ass)
    under = [w for a, b, w in caps if a < 2.0]
    lines = caption_lines_in(ass)
    print(f"       captions {lines}")
    # Nov 26: captions are lines of 2-3 words. CAN YOU is up from its first word until a moment after YOU (0.53 + 0.25 s):
    # the card words said next get no caption, so it doesn't stay up over them (it was YOU alone, 0.28-0.56); and no
    # caption shown under the card runs past the card's end, so OVERPASS never shows while the card does
    return (under == ["CAN", "YOU", "OR", "THE"] and [w for a, b, w in caps if a >= 2.0] == ["OVERPASS", "THAT", "OVERPASS",
                                                                                          "JUMP", "HURT"]
            and lines[0] == (0.0, 0.78, "CAN YOU") and all(b <= 2.0 + 1e-6 for a, b, _ in lines if a < 2.0)
            and not why
            and report == {"shown": 9, "over_a_shot": 9, "tested": 0, "unknown_things": ["OVERPASS"], "on_card": 4})


@check
def the_caption_check_allows_only_what_the_card_shows():
    """a word may lack its caption only while the open card shows it. Each of these fails: a word that isn't on the card
    with no caption under it, a card word with none after the card is gone, a card word captioned under the card, and
    the same captions with no card"""
    with tempfile.TemporaryDirectory() as tmp:
        ass, judge = built_captions(tmp)
        lines = ass.splitlines()

        def holding(word, after=0.0):            # the caption event that shows this word (one word or a line of them)
            return next(k for k, x in enumerate(lines) if ",Caption," in x
                        and word in [w for *_, w in captions_in(x)] and captions_in(x)[0][0] >= after)

        def without(word, after):
            k = holding(word, after)
            left = edited_caption(lines[k], word)
            return "\n".join(lines[:k] + ([left] if left else []) + lines[k + 1:])
        at = holding("OR")
        twice = lines[holding("THE")]            # SURVIVE alone, captioned under the card, just before OR's caption
        for w in [w for *_, w in captions_in(twice)]:
            twice = edited_caption(twice, w, "SURVIVE") if w == "THE" else edited_caption(twice, w)
        both = judge("\n".join(lines[:at] + [twice] + lines[at:]))[1]
        bare = judge("\n".join(x for x in lines if ",Title," not in x))[1]
        or_gone, late_gone = judge(without("OR", 0.0))[1], judge(without("JUMP", 3.0))[1]
    return (or_gone == ["the caption is 'the' where the line says 'or'"]
            and late_gone == ["the caption is 'hurt' where the line says 'jump'"]
            and both == ["the caption is 'survive' where the line says 'or'"]
            and bare == ["the caption is 'or' where the line says 'survive'"])


# --- 11. The captions (the owner, Nov 22; built Nov 26): 2-3 white words a line, 1-2 accent words, in the safe area ---

LINE_TEST = [{"say": "Watch the red pickup on the bridge."}, {"say": "It has one lane left, then the traffic stops."},
             {"say": "Nope."}, {"say": "Watch that pickup again."}]
WHITE_ASS, YELLOW_ASS, CYAN_ASS = "&HFFFFFF&", "&H00E6FF&", "&HFFE500&"


def line_words(lines=LINE_TEST, pause_after=("left,",)):
    """assemble's word times for these lines, one after the other (0.28 s a word, 0.3 s between lines); a word in
    pause_after is followed by a pause (a comma), as Kokoro's tokens mark it."""
    import assemble
    spoken = fake_voice([])(lines, 1.2)
    marks = [w.lower() for w in pause_after]
    for sp, line in zip(spoken, lines):
        toks = line["say"].split()
        for w, tok in zip(sp["words"], toks):
            w["pause"] = tok.lower() in marks
    placed, t = [], 0.0
    for n, sp in enumerate(spoken, 1):
        placed.append({"line": n, "start": round(t, 3), "end": round(t + sp["seconds"], 3)})
        t += sp["seconds"] + 0.3
    return assemble.voice_track(spoken, placed, t)[2], t


def line_events_of(ass):
    """[(start, end, [(word, colour)], override tags)] of the caption events in an .ass file."""
    out = []
    for raw in ass.splitlines():
        if not raw.startswith("Dialogue") or ",Caption," not in raw:
            continue
        f = raw.split(",", 9)
        h1, m1, s1 = f[1].split(":")
        h2, m2, s2 = f[2].split(":")
        tags = re.match(r"\{([^}]*)\}", f[9]).group(1)
        words, colour = [], None
        for block, text in re.findall(r"\{([^}]*)\}([^{]*)", f[9]):
            c = re.findall(r"\\c(&H[0-9A-F]{6}&)", block)
            colour = c[-1] if c else colour
            words += [(w, colour) for w in text.split()]
        out.append((int(h1) * 3600 + int(m1) * 60 + float(s1), int(h2) * 3600 + int(m2) * 60 + float(s2), words, tags))
    return out


@check
def captions_are_two_or_three_white_words_with_one_or_two_accents():
    """assemble's captions for 4 lines (21 words): every word once, in order; each caption 2-3 words, never across a
    sentence, broken at the comma, one word only for the one-word sentence (NOPE.); every word white but 1-2 accent
    words a caption, yellow or cyan: PICKUP (a thing to watch) cyan, the key word (WATCH, which the open card and the
    hook share) yellow; a thick black outline (12), Lilita One at 120 at most, centred on x 510 and the old word line (y 1260); each
    caption is up from its first word until the next one starts, or a moment after a pause"""
    import assemble
    import explainer as ex
    words, total = line_words()
    events = line_events_of(ex.ass_document(assemble.caption_events(words, total, None, keys=["watch"])))
    flat = [w for _, _, ws, _ in events for w, _ in ws]
    sizes = [len(ws) for _, _, ws, _ in events]
    said = [w["text"] for w in words]
    k, crossing = 0, False
    for _, _, ws, _ in events:
        crossing |= len({words[k + j]["sentence"] for j in range(len(ws))}) > 1
        k += len(ws)
    accents = [[(w, c) for w, c in ws if c != WHITE_ASS] for _, _, ws, _ in events]
    colours = {w: c for _, _, ws, _ in events for w, c in ws}
    styled = all(re.search(r"\\bord1[2-9]", t) and "\\3c&H000000&" in t and "\\pos(510," in t
                 and int(re.search(r"\\fs(\d+)", t).group(1)) <= 120 and "\\fnLilita One" in t for *_, t in events)
    ys = [int(re.search(r"\\pos\(510,(\d+)\)", t).group(1)) for *_, t in events]
    comma = next(ws for _, _, ws, _ in events if any(w == "LEFT" for w, _ in ws))
    timed = all(abs(a - words[sum(sizes[:i])]["start"]) < 0.011 for i, (a, *_) in enumerate(events))
    print(f"       captions {[' '.join(w for w, _ in ws) for _, _, ws, _ in events]}; accents {accents}; y {set(ys)}")
    return (flat == said and len(said) == 21 and not crossing and timed
            and all(2 <= n <= 3 for n, (_, _, ws, _) in zip(sizes, events) if ws[0][0] != "NOPE")
            and [ws for _, _, ws, _ in events if ws[0][0] == "NOPE"][0] == [("NOPE", YELLOW_ASS)]
            and comma[-1][0] == "LEFT"
            and all(1 <= len(a) <= 2 and all(c in (YELLOW_ASS, CYAN_ASS) for _, c in a) for a in accents)
            and colours["PICKUP"] == CYAN_ASS and colours["WATCH"] == YELLOW_ASS and colours["THE"] == WHITE_ASS
            and styled and all(abs(y - 1260) <= 40 for y in ys))


@check
def caption_lines_stay_inside_the_safe_area_on_real_pixels():
    """burnt onto a black 1080x1920 frame at each caption's middle, the ink (outline and shadow in) stays inside x 60-960
    and y 1185-1345, the band the word captions had, even for the widest line; white, yellow and cyan are all there
    (the key word SURVIVE, the thing to watch PICKUP)"""
    import numpy as np
    import assemble
    import explainer as ex
    words, total = line_words([{"say": "Survive this overpass jump."}, {"say": "Nobody expected this pickup."}])
    events = assemble.caption_events(words, total, None, keys=["survive"])
    with tempfile.TemporaryDirectory() as tmp:
        for font in ex.FONT_FILES:
            shutil.copy(font, tmp)
        Path(tmp, "c.ass").write_text(ex.ass_document(events), encoding="utf-8")
        boxes, seen = [], set()
        for a, b, ws, _ in line_events_of(ex.ass_document(events)):
            t = (a + b) / 2
            raw = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=black:s=1080x1920:r=30:d={total + 1}",
                                  "-vf", "ass=c.ass:fontsdir=.", "-ss", f"{t:.2f}", "-frames:v", "1", "-f", "rawvideo",
                                  "-pix_fmt", "rgb24", "-"], cwd=tmp, capture_output=True).stdout
            f = np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)
            ys, xs = np.nonzero(f.max(axis=2) > 24)
            boxes.append((xs.min(), ys.min(), xs.max(), ys.max()))
            if ((f > 230).all(axis=2)).sum() > 200:
                seen.add("white")
            if ((f[..., 0] > 230) & (f[..., 1] > 200) & (f[..., 2] < 60)).sum() > 200:
                seen.add("yellow")
            if ((f[..., 0] < 60) & (f[..., 1] > 200) & (f[..., 2] > 230)).sum() > 200:
                seen.add("cyan")
    print(f"       ink boxes {boxes}; colours {sorted(seen)}")
    return (all(60 <= x0 and x1 <= 960 and 1185 <= y0 and y1 <= 1345 for x0, y0, x1, y1 in boxes)
            and seen == {"white", "yellow", "cyan"})


@check
@testkit.full
def a_real_v4_render_counts_its_captions():
    """plain_render, a whole Short: render.json's checks say its 12 captioned words were seen (of 13 words: WATCH is on
    the open card, PR 33; line 1 is "Watch closely." since Nov 26, it was "Watch this." with 2 on the card), each over a
    shot, and the one the check knows was asked about its frame and passed; the review page says so too"""
    made = plain_render()
    checks = made.entry["checks"]
    return (checks["captions"] == {"shown": 12, "over_a_shot": 12, "tested": 1, "unknown_things": [], "on_card": 1}
            and len(made.log["word_times"]) == 13 and [c["word"] for c in checks["caption_checks"]] == ["CRASH"]
            and all(c["ok"] for c in checks["caption_checks"])
            and "12 words shown" in (made.folder / "review.html").read_text(encoding="utf-8"))


@check
def a_bad_caption_fails_the_whole_v4_render():
    """launch.render with engine v4 and captions that say TINY where the voice says HUGE: the Short is FAILED and the
    problem names the word (the shots and the build are small stand-ins; the captions and their checks are real)"""
    import numpy as np

    def make_shot(video, s, path, grade, log):
        make_video(path, 12, size="108x192")
        return {"pieces": []}

    def build(out_dir, video, shots, lines, spoken, placed, *rest):
        import assemble
        import explainer as ex
        total = sum(s["frames"] for s in shots) / cutplan.FPS
        _, _, words = assemble.voice_track(spoken, placed, total)
        burnt = [dict(w, text="TINY") if w["text"] == "HUGE" else w for w in words]
        (Path(out_dir) / "captions.ass").write_text(ex.ass_document(ex.word_events(burnt, total)), encoding="utf-8")
        return {"word_times": [{"text": w["text"], "start_s": w["start"], "end_s": w["end"]} for w in words],
                "visuals": assemble.visuals(shots, placed, total)}
    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "x.webm"
        folder = run_folder_with(tmp, [moment(video)], [short()])
        data = launch.render(folder, engine="v4", checker=FakeChecker(), indexes=[made_up_idx("synth", video)],
                             facts=FACTS, today=TODAY, shorts_dir=Path(tmp), log=lambda m: None,
                             v4_parts={"synth": fake_voice([]), "make_shot": make_shot, "build": build,
                                       "light": lambda v, s: np.full((16, 480, 270), 150, np.uint8),
                                       "loudest": lambda v, a, b: 21.0, "cuts": lambda v, a, b: [],
                                       "clear": lambda v, a, latest, focus: (a, [], [])})
        e = data["shorts"][0]
    return (e["status"] == "FAILED" and len(e["problems"]) == 1
            and has(e["problems"], "the caption is 'tiny' where the line says 'huge'"))


# --- PR 30 (Nov 23): the low-light grade ---

OLD_GRADE = ",eq=contrast=1.09:saturation=1.25,vignette=angle=0.35"     # pinned: what every v4 shot had until Nov 23


def light_video(path, level, seconds=2):
    """A made-up 16:9 recording: a fine texture round one brightness (a sunset street ~60, a day street ~150)."""
    src = f"color=c=0x{level:02x}{level:02x}{level:02x}:size=1920x1080:rate=30:duration={seconds}"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-vf",
                    "noise=alls=40:allf=u,format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "12", "-colorspace", "bt709", "-color_range", "tv", str(path)], check=True, capture_output=True)
    return path


def graded_mean(video, tmp, grade, name, info):
    import proof
    made = steady.make_piece(video, 0.2, 15, Path(tmp) / f"{name}.mp4", info, grade=grade)
    return proof.frame_stats(proof.clip_frames(made, [0.25])[0], 270, 480)["mean"]


@check
@testkit.full
def a_dark_shot_is_not_darkened():
    """a dark made-up shot (about 53, M24's landing): the old grade darkens it; the low-light grade has no contrast or
    vignette, is never darker than the ungraded picture and brings it over the check's minimum, which is still 55 and
    still measured on the graded clip; a dim one (about 60) isn't darker either"""
    import proof
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for level in (53, 65):
            v = light_video(Path(tmp) / f"v{level}.mp4", level)
            info = steady.info_of(v)
            grade, rep = steady.shot_grade(steady.light_frames(v, 0.0, 1.5, info), OLD_GRADE)
            none, old, new = (graded_mean(v, tmp, g, f"{n}{level}", info)
                              for g, n in (("", "none"), (OLD_GRADE, "old"), (grade, "new")))
            print(f"       level {level}: ungraded {none:.1f}, old grade {old:.1f}, new {new:.1f} (gamma {rep['gamma']})")
            out.append(rep["low_light"] and "contrast" not in grade and "vignette" not in grade and old < none - 5
                       and new >= none and abs(rep["level"] - none) < 1.0)
            if level == 53:
                out.append(none < 55 and proof.DARK_MEAN == 55 and new > none and new >= 55)
    return all(out)


@check
def a_daytime_shot_is_graded_exactly_as_before():
    """a bright made-up shot gets the old grade, letter for letter, and the made clip is the same picture as the old
    grade's, byte for byte; the line is one constant (70): just above it nothing changes"""
    import numpy as np
    import explainer
    with tempfile.TemporaryDirectory() as tmp:
        v = light_video(Path(tmp) / "day.mp4", 150)
        info = steady.info_of(v)
        grade, rep = steady.shot_grade(steady.light_frames(v, 0.0, 1.5, info), explainer.graded(True))
        made = [steady.make_piece(v, 0.2, 10, Path(tmp) / f"{n}.mp4", info, grade=g)
                for n, g in (("now", grade), ("before", OLD_GRADE))]
        raw = [subprocess.run(["ffmpeg", "-v", "error", "-i", str(x), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                              capture_output=True).stdout for x in made]

    def flat(n):
        return np.full((16, 480, 270), n, np.uint8)
    over, _ = steady.shot_grade(flat(71), OLD_GRADE)
    under, _ = steady.shot_grade(flat(69), OLD_GRADE)
    return (grade == OLD_GRADE == explainer.graded(True) and not rep["low_light"] and raw[0] == raw[1]
            and len(raw[0]) == 10 * 1080 * 1920 * 3 // 2 and steady.LOW_LIGHT == 70 and over == OLD_GRADE
            and under != OLD_GRADE)


@check
def the_lift_is_capped_and_black_stays_black():
    """a night-dark shot gets the strongest lift and no more (gamma 1.2, at most 18 of 255), and stays under the check;
    on real video levels through ffmpeg, black (16) and white (235) don't move, the darkest shadows (to 2 % of white)
    move at most 1, nothing gets darker, the order of the greys is kept and the colour planes are untouched"""
    import numpy as np
    night = np.random.default_rng(1).integers(0, 50, (16, 480, 270)).astype(np.uint8)
    grade, rep = steady.shot_grade(night, OLD_GRADE)
    x = np.arange(256) / 255
    rise = 255 * (steady.lift_curve(x, steady.LIFT_MAX) - x)
    out = subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", "256x2", "-i", "-",
                          "-vf", steady.lift_filter(steady.LIFT_MAX), "-f", "rawvideo", "-"],
                         input=bytes(range(256)) * 2 + bytes([128]) * 256, capture_output=True).stdout
    y = np.frombuffer(out[:256], np.uint8).astype(int)
    move = y[16:236] - np.arange(16, 236)
    return (steady.LIFT_MAX == 1.2 and rep["gamma"] == 1.2 and steady.lift_filter(1.2) in grade and rep["lifted"] < 55
            and rise.max() <= 18 and y[16] == 16 and y[235] == 235 and (move >= 0).all() and move[:5].max() <= 1
            and move.max() <= 16 and (np.diff(y[16:236]) >= 0).all() and out[512:] == bytes([128]) * 256)


@check
def a_shot_that_ends_in_the_dark_is_dark_and_a_flash_of_dark_is_not():
    """M24's shot starts in the sun (80) and lands in the shade (54): the quarter of its frames in the dark decides, so
    the landing is not darkened. A day shot with 2 dark frames of 16 is graded as always; nothing measured is too"""
    import numpy as np

    def shot(levels):
        return np.stack([np.full((480, 270), n, np.uint8) for n in levels])
    m24, rep = steady.shot_grade(shot([80] * 11 + [54] * 5), OLD_GRADE)
    flash, _ = steady.shot_grade(shot([120] * 14 + [30] * 2), OLD_GRADE)
    nothing, r0 = steady.shot_grade(np.zeros((0, 480, 270), np.uint8), OLD_GRADE)
    return (m24 != OLD_GRADE and rep["low_light"] and 1 < rep["gamma"] <= 1.1 and rep["lifted"] >= steady.LIFT_TO > 55
            and flash == OLD_GRADE and nothing == OLD_GRADE and r0["level"] is None)


@check
def render_grades_each_shot_by_its_own_light_and_the_replay_like_its_payoff():
    """launch.render_v4: a dark payoff shot gets the low-light grade, its replay the same grade (not its own), a day
    shot the old one"""
    import numpy as np
    import explainer
    asked, grades = [], []

    def light(video, s):
        asked.append(s["n"])
        return np.full((16, 480, 270), 54 if s["n"] == 1 else 150, np.uint8)

    def make_shot(video, s, path, grade, log):
        grades.append((s["n"], bool(s.get("replay")), grade))
        return {"pieces": []}
    with tempfile.TemporaryDirectory() as tmp:
        one = short(shots=[{"from": 14.0, "to": 22.5}, {"from": 30.0, "to": 36.0}], replay={"from": 20.6, "to": 21.4},
                    lines=short()["lines"][:2] + [{"say": "Hold for it.", "claim": "hype", "at": 30.2}] + short()["lines"][3:])
        folder = run_folder_with(tmp, [moment(Path(tmp) / "x.webm")], [one])
        data = launch.render(folder, engine="v4", checker=FakeChecker(), indexes=[made_up_idx("synth", Path(tmp) / "x.webm")],
                             facts=FACTS, today=TODAY, shorts_dir=Path(tmp), log=lambda m: None,
                             v4_parts={"synth": fake_voice([]), "make_shot": make_shot, "light": light,
                                       "loudest": lambda v, a, b: 21.0, "cuts": lambda v, a, b: [],
                                       "clear": lambda v, a, latest, focus: (a, [], [])})
        e = data["shorts"][0]
    if len(grades) != 3:
        raise AssertionError("; ".join(e["problems"]))
    (_, r1, g1), (_, r2, g2), (_, r3, g3) = grades
    return (asked == [1, 3] and not r1 and r2 and not r3 and g1 == g2 and g1.startswith(",eq=saturation=1.25,lutyuv")
            and g3 == explainer.graded(True))


# --- 10. The production defaults (PR 31: test-only speed settings must not touch them) ---

GOLDEN = HERE / "testdata" / "v4_commands.json"


def plain_command(args, made):
    """A command with what differs from run to run and PC to PC taken out: where ffmpeg is installed, and the folders."""
    out = [Path(args[0]).stem.lower()]
    places = [(made.folder, "<run>"), (ROOT, "<tmp>"), (HERE, "<repo>"), (Path(tempfile.gettempdir()), "<temp>")]
    for a in args[1:]:
        for path, name in places:
            for form in (str(path), path.resolve().as_posix(), path.as_posix()):
                a = a.replace(form, name)
        a = re.sub(r"volume=-?[0-9.]+dB", "volume=<measured>dB", a)      # a gain follows from a loudness ffmpeg measured
        out.append(re.sub(r"<temp>[\\/][^\\/:]+", "<temp>/x", a).replace("\\", "/"))
    return out


def v4_commands():
    """Every command the v4 path runs for the fixture Short, plain and with its replay."""
    return {made.name: [plain_command(c, made) for c in made.commands] for made in (plain_render(), replay_render())}


@check
@testkit.full
@testkit.needs("nvenc")
def v4_runs_the_same_commands_as_before_the_speed_work():
    """every ffmpeg and ffprobe command that engine v4 builds with its default settings, for the fixture Short plain and
    with its replay, is the one main built before PR 31 (testdata/v4_commands.json, recorded from main 31d6ab7):
    sizes, crops, filter graphs, encoder settings, all of it, in order"""
    got, want = v4_commands(), json.loads(GOLDEN.read_text(encoding="utf-8"))["commands"]
    for name in want:
        for k, (a, b) in enumerate(zip(got[name], want[name])):
            if a != b:
                raise AssertionError(f"{name}, command {k + 1}: {' '.join(a)[:400]} (main: {' '.join(b)[:400]})")
    return got == want and all(len(v) > 20 for v in want.values())


def main():
    ok = True
    for fn in testkit.selected(CHECKS):
        try:
            got = fn()
            good, err = bool(got), ""
        except Exception as e:
            good, err = False, f"  ({type(e).__name__}: {e})"
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'} {fn.__name__}: {' '.join((fn.__doc__ or '').split())}{err}", flush=True)
    print(f"RESULT: {'PASS' if ok else 'FAIL'} ({len(testkit.selected(CHECKS))} checks)")
    _TMP.cleanup()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    import heavy
    heavy.main_guard("editortest.py")
    main()
