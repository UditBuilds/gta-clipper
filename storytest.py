"""Checks for the story layer (PR 26): edit2.py (edit engine v2), truth.py's story rules and launch.py's quality gate,
formats and v2 render. The voice is made up here (a tone per word), so no Kokoro is needed to plan; one check renders a
real Short from a made-up video with ffmpeg.

  .venv-tts\\Scripts\\python.exe storytest.py
"""

import json
import sys
import tempfile
from pathlib import Path

import edit2
import explainer as ex
import launch
import truth
from launchtest import FACTS, TODAY, chase_pick, made_up_idx, made_up_rec, run_folder_with

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


# --- A made-up voice: each word 0.28 s, 0.12 s between sentences, a tone while it speaks ---

WORD_S, GAP_S, RATE = 0.28, 0.12, 24000


def fake_synth(lines, voice, speed):
    import numpy as np
    words, parts, t, sentence = [], [], 0.0, 0
    for line in lines:
        for s in ex.split_sentences(line["say"]):
            for tok in ex.spoken_tokens(s):
                words.append({"text": tok.strip(".,;:?!\"'").upper(), "key": ex.word_key(tok), "start": t,
                              "end": t + WORD_S - 0.03, "sentence": sentence, "pause": tok[-1] in ",;:",
                              "colour": None, "tag": None})
                n = round(WORD_S * RATE)
                parts.append((0.3 * np.sin(2 * np.pi * 220 * np.arange(n) / RATE)).astype(np.float32))
                t += WORD_S
            parts.append(np.zeros(round(GAP_S * RATE), np.float32))
            t += GAP_S
            sentence += 1
    return np.concatenate(parts), RATE, words, [0.0], 0


VIDEO_DIR = tempfile.TemporaryDirectory(prefix="storytest_")
VIDEO = Path(VIDEO_DIR.name) / "rec_a.mp4"


def made_up_video(path=None, seconds=140):
    """A 1920x1080 picture at 30 fps with a tone, and a small test-pattern card (texture, so it can be followed)
    that moves right at 6 px a second from x 400 (made once, shared by every check)."""
    if VIDEO.is_file():
        return True
    graph = (f"color=c=0x203040:s=1920x1080:r=30:d={seconds}[bg];testsrc2=s=80x80:r=30:d={seconds}[sq];"
             "[bg][sq]overlay=x='400+6*t':y=500[v]")
    return launch.subprocess.run(["ffmpeg", "-v", "error", "-y", "-filter_complex", graph, "-f", "lavfi", "-i",
                                  f"sine=f=300:d={seconds}", "-map", "[v]", "-map", "0:a", "-c:v", "libx264",
                                  "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(VIDEO)],
                                 capture_output=True).returncode == 0


def script_on(video, lines, window=(5.0, 50.0), action=12.0, brk=None, pill=True):
    s = {"title": "story test", "music": None, "game_audio": False, "profile": "gta5",
         "moment": {"file": str(video), "window": list(window), "focus": 0.5, "action_s": action}, "lines": lines}
    if pill:
        s["end_pill"] = "@NeonGetaway • Subscribe"
    if brk:
        s["break"] = brk
    return s


SAY = ["Can the cops find a white box in the dark?", "The box moves right along the road, and it never stops.",
       "Here it passes the middle of the picture and keeps going.", "This is where the cops lose sight of it.",
       "It ends up at the far side, still moving to the right.", "And that is how the box gets away, so watch"]


def lines_with(**extra):
    out = [{"say": s} for s in SAY]
    for k, v in extra.items():
        out[int(k[1:]) - 1].update(v)
    return out


@check
def shots_are_slow_cuts_of_one_moment_in_time_order():
    """edit2.plan(): the background is cut at word starts into shots of 3.5-5.0 s (the payoff starts a held shot
    of its own, up to 6 s), all from the moment's one
    recording inside its window, each starting at or after the end of the one before; the loop ending plays the
    footage just before the first frame; captions default to yellow"""
    with tempfile.TemporaryDirectory() as tmp:
        video = VIDEO
        if not made_up_video():
            return False
        script = edit2.load(script_on(video, lines_with(l5={"payoff": True})))
        tl = edit2.plan(script, fake_synth)
    starts = {round(w["start"], 3) for w in tl["words"]}
    foot = [s for s in tl["shots"] if s["kind"] == "footage"]
    order = all(b["src"] >= a["src"] + a["frames"] / 30 - 1e-3 for a, b in zip(foot, foot[1:]))
    inside = all(5.0 - 1e-6 <= s["src"] and s["src"] + s["frames"] / 30 <= 50.0 + 1e-3 for s in foot)
    held = tl["payoff_span"][0]
    lengths = all(3.45 <= s["frames"] / 30 <= (edit2.PAYOFF_SHOT_MAX_S if s["start"] <= held < s["end"] else 5.0) + 0.05
                  for s in foot) and any(abs(s["start"] - held) < 1e-3 for s in foot)
    cuts_on_words = all(round(s["start"], 3) in starts for s in foot[1:])
    loop = tl["shots"][-1]
    return (tl["problems"] == [] and len(foot) >= 3 and order and inside and lengths and cuts_on_words
            and loop["kind"] == "loop" and abs(loop["src"] + loop["frames"] / 30 - foot[0]["src"]) < 0.05
            and script["caption_style"] == "yellow")


@check
def a_named_thing_is_on_screen_within_half_a_second_of_its_word():
    """an anchor late in the window makes its shot skip forward (never back) so the evidence is on screen within
    0.5 s of the word; an anchor the time order can't keep (earlier than footage already shown) is a problem that
    names the line and the word"""
    with tempfile.TemporaryDirectory() as tmp:
        video = VIDEO
        if not made_up_video():
            return False
        good = lines_with(l3={"anchors": [{"word": "middle", "from_s": 30.0, "to_s": 31.0, "evidence": "E9"}]})
        tl = edit2.plan(edit2.load(script_on(video, good)), fake_synth)
        bad = lines_with(l2={"anchors": [{"word": "road", "from_s": 30.0, "to_s": 31.0, "evidence": "E9"}]},
                         l4={"anchors": [{"word": "cops", "from_s": 8.0, "to_s": 9.0, "evidence": "E2"}]})
        tl2 = edit2.plan(edit2.load(script_on(video, bad)), fake_synth)
    x = tl["anchors"][0]
    return (x["ok"] and 29.5 <= x["shown_s"] <= 31.5 and tl["problems"] == []
            and any("line 4: \"cops\"" in p for p in tl2["problems"]))


@check
def the_first_two_seconds_show_the_setup():
    """a moment whose action comes in the Short's first 2 s is refused: the opening must show the situation before
    it"""
    with tempfile.TemporaryDirectory() as tmp:
        video = VIDEO
        if not made_up_video():
            return False
        tl = edit2.plan(edit2.load(script_on(video, lines_with(), action=6.0)), fake_synth)
        ok = edit2.plan(edit2.load(script_on(video, lines_with(), action=9.0)), fake_synth)
    return any("situation before the action" in p for p in tl["problems"]) and ok["problems"] == []


@check
def pops_land_on_their_words_and_every_long_gap_is_logged():
    """a pop starts 0.08 s before its word and ends before the next; pops closer than 1.5 s are a problem; every gap
    over 2.0 s is logged with why (the payoff hold, the break, or nothing meaningful fits); each pop gets its own
    sound (circle: lock-on click, stat: hit, pip: whoosh)"""
    with tempfile.TemporaryDirectory() as tmp:
        video = VIDEO
        if not made_up_video():
            return False
        lines = lines_with(l2={"pops": [{"kind": "circle", "on": "box", "box": [440, 500, 80, 80], "at": 6.0}]},
                           l3={"pops": [{"kind": "stat", "on": "middle", "text": "the middle"}]},
                           l5={"payoff": True})
        tl = edit2.plan(edit2.load(script_on(video, lines)), fake_synth)
        close = lines_with(l2={"pops": [{"kind": "stat", "on": "box", "text": "a box"},
                                        {"kind": "stat", "on": "moves", "text": "it moves"}]})
        tl2 = edit2.plan(edit2.load(script_on(video, close)), fake_synth)
    a, b = tl["ranges"][1]
    box = next(w for w in tl["words"][a:b] if w["key"] == "BOX")
    circle = tl["pops"][0]
    sfx = edit2.sfx_events(tl["pops"], None, None)
    whys = " ".join(g["why"] for g in tl["gaps"])
    return (abs(circle["start"] - (box["start"] - 0.08)) < 1e-6 and circle["end"] <= tl["pops"][1]["start"]
            and any("1.5 s apart" in p for p in tl2["problems"]) and "payoff hold" in whys
            and all(g["seconds"] > 2.0 for g in tl["gaps"])
            and [e["kind"] for e in sfx] == ["lock", "hit"])


@check
def overlays_and_breaks_must_explain():
    """load() refuses a circle with no box, a stat pill of more than 4 words, a break with no label or a freeze
    with nothing to show (the deadpan joke break of the S4 sample is no longer possible); a freeze names its frame
    and detail"""
    with tempfile.TemporaryDirectory() as tmp:
        video = VIDEO
        if not made_up_video():
            return False
        bad = lines_with(l2={"pops": [{"kind": "circle", "on": "box"}]},
                         l3={"pops": [{"kind": "stat", "on": "middle", "text": "one two three four five"}]})
        msgs = []
        for brk in (None, {"after_line": 2, "text": "it's a truck, not a boat."},
                    {"after_line": 2, "kind": "freeze", "label": "the box"}):
            try:
                edit2.load(script_on(video, bad if brk is None else lines_with(), brk=brk))
            except edit2.EditError as e:
                msgs.append(str(e))
        good = edit2.load(script_on(video, lines_with(), brk={"after_line": 2, "kind": "freeze", "at_s": 8.0,
                                                               "box": [460, 500, 80, 80], "label": "the white box"}))
    return (len(msgs) == 3 and "needs \"box\"" in msgs[0] and "1-4 words" in msgs[0]
            and "names what it shows" in msgs[1] and "key detail" in msgs[2] and good["break"]["kind"] == "freeze")


@check
def a_circle_follows_its_target_and_stops_when_lost():
    """edit2.follow() keeps a moving target (the square going right) within a few pixels for 2 s, its match staying
    high; the same box placed on empty background is lost (no circle when we aren't sure where the thing is)"""
    if not made_up_video():
        return False
    path = edit2.follow(VIDEO, 10.0, 2.0, 400 + 6 * 10 + 40, 540, 40, (1920, 1080))
    expected = 400 + 6 * path[-1][0] + 40
    lost = edit2.follow(VIDEO, 10.0, 2.0, 1500, 300, 40, (1920, 1080))
    return (len(path) >= 55 and abs(path[-1][1] - expected) < 6 and min(m for *_, m in path) >= edit2.TRACK_MIN
            and len(lost) < 10)


@check
def a_real_story_short_renders():
    """render_plan() on a made-up video with the made-up voice: 1080x1920 at its planned length, with a followed
    circle, a hud card, a stat pill, a freeze that shows its detail and the subscribe pill; true peak under -1.0
    dBTP; the log has the shots, anchors, pops and every gap over 2 s"""
    with tempfile.TemporaryDirectory() as tmp:
        video, out = VIDEO, Path(tmp) / "out"
        if not made_up_video():
            return False
        lines = lines_with(l1={"pops": [{"kind": "pip", "on": "cops", "hud": "stars"}]},
                           l2={"pops": [{"kind": "circle", "on": "box", "box": [440, 500, 80, 80], "at": 6.0}]},
                           l3={"pops": [{"kind": "stat", "on": "middle", "text": "the middle"}]},
                           l5={"payoff": True})
        script = edit2.load(script_on(video, lines, brk={"after_line": 3, "kind": "freeze", "at_s": 16.0,
                                                          "box": [1320, 500, 80, 80], "label": "the white box"}))
        tl = edit2.plan(script, fake_synth)
        data = edit2.render_plan(script, tl, out, log=lambda m: None)
        info = launch.subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height:format=duration",
                                      "-of", "json", str(out / "short.mp4")], capture_output=True, text=True)
        probe = json.loads(info.stdout)
        ass = (out / "captions.ass").read_text(encoding="utf-8")
    size = (probe["streams"][0]["width"], probe["streams"][0]["height"])
    return (size == (1080, 1920) and abs(float(probe["format"]["duration"]) - tl["total_s"]) < 0.15
            and data["loudness"]["true_peak_guard"]["after"] < -1.0 and not any(p.get("skipped") for p in data["pops"])
            and "THE WHITE BOX" in ass and "\\1a&HFF&" in ass and data["break"]["kind"] == "freeze"
            and data["caption_style"] == "yellow" and len(data["background"]["shots"]) == len(tl["shots"]))


# --- truth.py: the story rules ---

def story_short(pick, **over):
    """A narrated story Short on the launchtest chase that keeps every story rule."""
    first, two, crash = (next(e["id"] for e in pick["evidence"] if e["kind"] == k and v in str(e["value"]))
                         for k, v in (("stars", "0 to 1"), ("stars", "1 to 2"), ("event", "crash")))
    police = next(e["id"] for e in pick["evidence"] if e["kind"] == "police" and e["from_s"] == 108.0)
    late = next(e["id"] for e in pick["evidence"] if e["kind"] == "police" and e["from_s"] >= 130)
    hook = "How fast do the cops come"
    short = {"short": 1, "moment": "M1", "type": "narrated", "hooks": [hook, "Two stars came fast", "The cops came fast"],
             "star": 0, "title": "How FAST the cops come after you in GTA", "description":
             "A police car is on me before the first star. Two stars and a crash follow in seconds.",
             "hashtags": ["#GTA6", "#GTAVI", "#Shorts"], "format": "F3",
             "format_why": "the chase shows the police, two star rises and a crash",
             "lines": [{"say": hook + "?", "claim": "ask", "evidence": [], "answered_by": 3},
                       {"say": "The first wanted star comes with a police car right behind me.", "claim": "on_screen",
                        "evidence": [first, police], "teaches": "the first star comes with the police close"},
                       {"say": "Seconds later the second star is on the HUD.", "claim": "on_screen",
                        "evidence": [two], "teaches": "how fast the second star follows",
                        "pops": [{"kind": "pip", "on": "star", "hud": "stars"}]},
                       {"say": "Then a police car crashes into a wall.", "claim": "on_screen", "evidence": [crash],
                        "teaches": "a chase can end with a police crash", "payoff": True,
                        "pops": [{"kind": "circle", "on": "crashes", "evidence": crash, "box": [900, 500, 80, 60],
                                  "at": 132.0}]},
                       {"say": "And that is how fast", "claim": "on_screen", "evidence": [late],
                        "teaches": "the loop back to the question", "loop": True}]}
    short["hooks"][0] = hook + "?"
    short.update(over)
    return short


@check
def a_story_short_that_keeps_the_rules_passes():
    """truth.check_short(): a question hook answered later, lines that each teach something (with "teaches"), one
    payoff, a hud card on a star word, a circle on the thing its line names and a loop line pass with no problem"""
    pick = chase_pick()
    problems, _, _ = truth.check_short(story_short(pick), pick, FACTS, TODAY)
    return problems == []


@check
def play_by_play_and_missing_story_parts_are_refused():
    """refused, each with a plain reason: a line that narrates what I do ("So I cut across ..."), an on_screen line
    without "teaches", a question no later line answers, no payoff, a last line without "loop" or ending in a full
    stop, a spoken "subscribe", a format the moment doesn't offer, and mission footage with no mission_ok"""
    pick = chase_pick()
    good = story_short(pick)
    bad = json.loads(json.dumps(good))
    bad["lines"][1]["say"] = "So I cut across the road with the police behind me."
    bad["lines"][3].pop("teaches")
    bad["lines"][0]["answered_by"] = None
    bad["lines"][3]["payoff"] = False
    bad["lines"][4]["say"] = "Subscribe for more."
    problems, _, _ = truth.check_short(bad, pick, FACTS, TODAY)
    text = " ".join(problems)
    offered = dict(pick, format_options=[{"format": "F2", "why": "x"}], mission=True)
    p2, _, _ = truth.check_short(good, offered, FACTS, TODAY)
    t2 = " ".join(p2)
    return ("line 2: play-by-play" in text and "line 4: \"teaches\"" in text and "line 1: a question needs" in text
            and "payoff: mark exactly one" in text and "full stop" in text and "line 5: no spoken call to action" in text
            and "format: F3 isn't one this moment offers" in t2 and "mission_ok" in t2)


@check
def overlays_must_name_what_they_show():
    """a circle on a word that doesn't name its evidence, a hud card on a line that cites no stars, and a pill that
    names nothing on screen ("KEEPS GOING") are refused; a freeze label must name its evidence"""
    pick = chase_pick()
    bad = story_short(pick)
    bad["lines"][3]["pops"][0]["on"] = "wall"
    bad["lines"][3]["pops"][0]["evidence"] = next(e["id"] for e in pick["evidence"] if e["kind"] == "stars")
    bad["lines"][1]["pops"] = [{"kind": "pip", "on": "star", "hud": "stars"}, {"kind": "stat", "on": "behind",
                                                                              "text": "keeps going"}]
    bad["lines"][1]["evidence"] = [e for e in bad["lines"][1]["evidence"] if not e.startswith("E7")][:1]
    bad["break"] = {"after_line": 2, "kind": "freeze", "evidence": next(e["id"] for e in pick["evidence"]
                                                                         if e["kind"] == "event"), "label": "wow"}
    problems, _, _ = truth.check_short(bad, pick, FACTS, TODAY)
    text = " ".join(problems)
    return ("line 4: a circle names the evidence it rings" in text and "HUD card shows the wanted stars" in text
            and "'keeps going' doesn't name anything" in text and "must name what the freeze shows" in text)


@check
def a_test_says_why_only_when_both_are_on_screen():
    """F2: a line that says why ("so the cops lost me", "by changing cars") is refused unless it cites two items in
    time order (the change, then the result); the same line with both cited passes that rule"""
    pick = chase_pick()
    first, two = (next(e["id"] for e in pick["evidence"] if e["kind"] == "stars" and v in e["value"])
                  for v in ("0 to 1", "1 to 2"))
    short = story_short(pick, format="F2")
    short["lines"][2]["say"] = "The second star came because the police saw me."
    one, _, _ = truth.check_short(short, pick, FACTS, TODAY)
    short["lines"][2]["evidence"] = [first, two]
    both, _, _ = truth.check_short(short, pick, FACTS, TODAY)
    return (any("line 3: says why" in p for p in one) and not any("line 3: says why" in p for p in both))


# --- launch.py: the gate, the formats and the v2 render ---

@check
def the_quality_gate_skips_a_moment_with_no_point():
    """story_gate(): the chase (star rises, police, a crash) offers F3 and an action payoff; a calm drive with
    nothing to teach is skipped with the reason; mission footage passes only with a payoff that needs no story;
    stars that rise and are then all gone offer F2 (a test with its answer on the HUD)"""
    rec = made_up_rec()
    chase = {"video": "rec_a", "start_s": 105.0, "end_s": 135.0, "span": [100.0, 160.0], "score": 5.0,
             "terms": {"action": 2.0}, "category": "chase"}
    calm = {"video": "rec_a", "start_s": 20.0, "end_s": 50.0, "span": [0.0, 100.0], "score": 1.0, "terms": {},
            "category": "driving"}
    ok1, _ = launch.story_gate(rec, chase, "gta5", True)
    formats = [f["format"] for f in chase["format_options"]]
    ok2, why2 = launch.story_gate(rec, calm, "gta5", True)
    mission = dict(calm, safety={"sheets": [{"from_s": 20.0 + 4 * k, "to_s": 24.0 + 4 * k,
                                             "answer": {"subtitles": True}} for k in range(8)]})
    ok3, why3 = launch.story_gate(rec, mission, "gta5", True)
    lost = launch.stars_lost(rec, 110.0, 175.0)
    return (ok1 and "F3" in formats and chase["point"]["action_payoff"] and not ok2 and "no clear point" in why2[0]
            and not ok3 and "mission footage" in why3[0] and lost == 160.0)


@check
def a_dry_run_stand_in_is_flagged_and_never_used_for_real():
    """F1's dry-run side is Rockstar's picture with a DRYRUN STAND-IN note (evidence R1); render refuses a stand-in
    outside a dry run"""
    side = launch.compare_side(launch.COMPARE_TOPICS[0], set(), "a city street at night", "gta5", True)
    if side is None:
        return False
    pick = dict(chase_pick(), format_options=[{"format": "F1", "why": "x", "compare": side}])
    try:
        launch.render_story(story_short(pick, format="F1"), pick, Path("."), FACTS, TODAY, [], Path("."), False)
        refused = False
    except launch.RenderProblem as e:
        refused = "DRYRUN STAND-IN" in str(e)
    return (side["standin"] and side["note"].endswith("DRYRUN STAND-IN") and side["evidence"][0]["id"] == "R1"
            and Path(side["file"]).is_file() and refused)


@check
def the_v2_render_checks_timing_and_logs_the_story():
    """render() with engine v2 (a made-up voice and render): every named thing is placed on screen with its word,
    the payoff lands at 45-75 %, the Short is READY with its format on the review page; a payoff too late or a Short
    too long is FAILED with the reason"""
    if not made_up_video():
        return False
    pick = dict(chase_pick(), path=str(VIDEO))
    good = story_short(pick)
    made = {}

    def slow_synth(lines, voice, speed):          # about 4 words in 2.6 s, so 41 words make a 25-40 s Short
        global WORD_S
        keep, WORD_S = WORD_S, 0.65
        try:
            return fake_synth(lines, voice, speed)
        finally:
            WORD_S = keep

    def fake_render(script, tl, out, log=print):
        made["script"], made["tl"] = script, tl
        launch.subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=108x192:d=1", "-f",
                               "lavfi", "-i", "sine=f=440:d=1", "-c:v", "libx264", "-c:a", "aac", "-shortest",
                               str(Path(out) / "short.mp4")], capture_output=True)
        return {"length_s": tl["total_s"], "pops": [], "gaps_over_2s": tl["gaps"], "pop_interval_s": None}
    long_short = story_short(pick)
    long_short["lines"][3]["say"] = "Then a police car crashes into a wall" + ", then a car hits a wall" * 9 + "."
    with tempfile.TemporaryDirectory() as tmp:
        shorts = Path(tmp) / "shorts"
        shorts.mkdir()
        folder = run_folder_with(tmp, [pick, dict(pick, short=2, id="M2")],
                                 [good, dict(long_short, short=2, moment="M2")], dry=True)
        data = launch.render(folder, shorts_dir=shorts, today=TODAY, log=lambda m: None, facts=FACTS, checker=None,
                             indexes=[made_up_idx(path=str(VIDEO))], synth=slow_synth, v2_render=fake_render)
        page = (folder / "review.html").read_text(encoding="utf-8")
    first, second = data["shorts"]
    anchors = made["tl"]["anchors"]
    return (first["status"] == "READY" and first["format"] == "F3" and 0.45 <= first["payoff_at"] <= 0.75
            and anchors and all(x["ok"] for x in anchors) and second["status"] == "FAILED"
            and any("a story Short is 25-40 s" in p for p in second["problems"]) and "F3" in page)


@check
def the_launch_night_experiments_cover_every_format():
    """shotlist.json's experiments: each names a story format, what to try and what must be on screen as proof, and a
    query library.py accepts; every format has at least one; every F1 lists the GTA 5 footage the library already has;
    the topics stay as they were (nothing here is a claim about GTA 6)"""
    import library
    data = library.load_shotlist()
    exps = data.get("experiments") or []
    for e in exps:
        library.check_query(e["query"])
    return (exps and {e["format"] for e in exps} == set(truth.FORMATS)
            and all(e["format"] in truth.FORMATS and e["try"].strip() and e["proof"].strip() for e in exps)
            and all(e.get("gta5_library") for e in exps if e["format"] == "F1")
            and "claim" not in json.dumps(data["topics"]).lower())


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
