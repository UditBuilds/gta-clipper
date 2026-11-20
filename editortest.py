"""Checks for editor-v3 (engine v4, Nov 20): payoff-first editing (cutplan.py), the measured picture (steady.py), the
Short put together (assemble.py) and launch.py's render_v4, one check per owner rule. The voice is made up (a tone per
word) and so is the vision model, so neither Kokoro nor Ollama is needed; the end-to-end check renders a real Short
from a made-up video with ffmpeg.

  .venv-tts\\Scripts\\python.exe editortest.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import cutplan
import launch
import steady
import truth
from launchtest import FACTS, TODAY, made_up_idx, run_folder_with

CHECKS = []


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
         "title": "This GTA 6 CRASH came out of nowhere", "description": "A car speeds down the road. Then it crashes.",
         "hashtags": ["#GTA6", "#GTAVI", "#Shorts"],
         "shots": [{"from": 14.0, "to": 25.0}, {"from": 30.0, "to": 36.0}],
         "lines": [{"say": "Watch this.", "claim": "hype", "at": 14.3},
                   {"say": "Wait for it.", "claim": "hype", "at": 17.5},
                   {"say": "That crash was huge.", "claim": "on_screen", "evidence": ["E2"],
                    "teaches": "the crash is the point", "at": 23.0},
                   {"say": "You just keep going.", "claim": "hype", "at": 30.3}]}
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
def no_voice_over_an_action_payoff():
    """in an action Short the game's own sound plays the payoff: a line over it is refused"""
    s = short(lines=[{"say": "Watch this.", "claim": "hype", "at": 14.3},
                     {"say": "Here it comes now.", "claim": "hype", "at": 19.8}])
    return has(problems_of(s), "talks over the payoff")


# --- 6. The voice at 1.2 and the punchy style ---

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
def the_voice_speed_is_1_2():
    """the owner's speed: engine v4 speaks at 1.2 unless the Short says otherwise"""
    return cutplan.SPEED == 1.2


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


@check
def end_to_end_a_v4_short_names_its_payoff():
    """launch.render with engine v4 on a made-up 40 s recording: READY, 17 s, the payoff named in render.json and on
    the review page, every shot once, the voice at 1.2, no word over a cut or over the payoff, the crash sound up"""
    with tempfile.TemporaryDirectory() as tmp:
        v = make_video(Path(tmp) / "synth.mp4", 40, loud_at=21.0, size="640x360")
        idx = made_up_idx("synth", v)
        pick = moment(v)
        folder = run_folder_with(tmp, [pick], [short()])
        speeds = []
        data = launch.render(folder, engine="v4", checker=FakeChecker(), indexes=[idx], facts=FACTS, today=TODAY,
                             shorts_dir=Path(tmp), log=lambda m: None,
                             v4_parts={"synth": fake_voice(speeds), "build": no_music_build})
        e = data["shorts"][0]
        if e["status"] != "READY":
            raise AssertionError("; ".join(e["problems"]))
        out = Path(e["folder"])
        log = json.loads((out / "log.json").read_text(encoding="utf-8"))
        page = (folder / "review.html").read_text(encoding="utf-8")
        cuts = log["cuts_s"]
        pay = e["payoff"]
        words_ok = all(not (w["start_s"] < c < w["end_s"]) for w in log["word_times"] for c in cuts)
        quiet = all(not (pay["at_s"] < w["start_s"] < pay["at_s"] + 2.5) for w in log["word_times"])
        return (abs(e["length_s"] - 17.0) < 0.1 and pay["what"] == "the car slams into the wall"
                and "slams into the wall" in page and speeds == [1.2] and words_ok and quiet
                and e["shown"] == [["synth", 14.0, 25.0], ["synth", 30.0, 36.0]]
                and (log["game_audio"].get("quiet_lift_db") is not None))


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
    print(f"RESULT: {'PASS' if ok else 'FAIL'} ({sum(1 for _ in CHECKS)} checks)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    import heavy
    heavy.main_guard("editortest.py")
    main()
