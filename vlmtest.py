"""Unit checks for the v6 veto (vlm.py) and v7's measured stars (--vlm-hint stars, --vlm-rule stars) on made-up
model answers. No Ollama, no footage and no ffmpeg are used.

Each check prints PASS or FAIL with what it tests. A check that raises counts as FAIL (with the error), so a
missing function shows as a failure, not a crash. The last line is RESULT: PASS or FAIL; exit code 0 or 1.

Usage:  python vlmtest.py
"""

import base64
import contextlib
import io
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import clip
import vlm

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


# --- Veto and fill ---

@check
def no_veto_keeps_the_first_five():
    """10 candidates, none vetoed: the clips are candidates 1-5 in rank order, no fillers"""
    return vlm.choose([False] * 10, 5) == [(i, False) for i in range(5)]


@check
def vetoed_candidates_are_skipped():
    """candidates 1 and 3 vetoed: the clips are 2, 4, 5, 6, 7 (the next ones move up), no fillers"""
    vetoed = [True, False, True] + [False] * 7
    return vlm.choose(vetoed, 5) == [(1, False), (3, False), (4, False), (5, False), (6, False)]


@check
def fillers_come_after_the_survivors():
    """7 of 10 vetoed (all but 3, 6, 9): survivors 3, 6, 9 first, then fillers 1 and 2 (best-ranked vetoed)"""
    vetoed = [i not in (2, 5, 8) for i in range(10)]
    return vlm.choose(vetoed, 5) == [(2, False), (5, False), (8, False), (0, True), (1, True)]


@check
def all_vetoed_gives_the_first_five_as_fillers():
    """all 10 vetoed: the clips are candidates 1-5, every one a filler"""
    return vlm.choose([True] * 10, 5) == [(i, True) for i in range(5)]


@check
def fewer_candidates_than_count():
    """only 3 candidates, the 2nd vetoed: 3 clips (1, 3, then 2 as filler); none is made up"""
    return vlm.choose([False, True, False], 5) == [(0, False), (2, False), (1, True)]


@check
def errors_never_veto():
    """a model error is not a veto, even with a veto category; only the 3 veto categories veto"""
    return (not vlm.is_veto({"error": "call failed", "category": "menu_or_cutscene"})
            and all(vlm.is_veto({"error": None, "category": c}) for c in vlm.VETO)
            and not any(vlm.is_veto({"error": None, "category": c}) for c in vlm.CATEGORIES if c not in vlm.VETO))


@check
def categories_and_veto_set():
    """6 categories in the schema enum; the veto set is clumsy_crash_or_stuck, boring_driving_or_walking,
    menu_or_cutscene; the schema asks for reason, then player_stalled, then category, all required"""
    s = vlm.SCHEMA
    return (s["properties"]["category"]["enum"] == list(vlm.CATEGORIES) and len(vlm.CATEGORIES) == 6
            and set(vlm.VETO) == {"clumsy_crash_or_stuck", "boring_driving_or_walking", "menu_or_cutscene"}
            and list(s["properties"]) == ["reason", "player_stalled", "category"]
            and s["required"] == ["reason", "player_stalled", "category"])


# --- The model's answer ---

@check
def a_good_answer_parses():
    """a reply that fits the schema gives reason, player_stalled and category (extra keys are dropped)"""
    a = vlm.parse_answer('{"reason": " A car jumps. ", "player_stalled": false, "category": "stunt_or_explosion", '
                         '"x": 1}')
    return a == {"reason": "A car jumps.", "player_stalled": False, "category": "stunt_or_explosion"}


@check
def bad_answers_are_errors():
    """not JSON, a JSON list, an unknown category, player_stalled not true/false, an empty reason: all ValueError"""
    bad = ["not json", "[1, 2]", '{"reason": "r", "player_stalled": false, "category": "explosion"}',
           '{"reason": "r", "player_stalled": "no", "category": "other_action"}',
           '{"reason": " ", "player_stalled": true, "category": "other_action"}']
    out = []
    for b in bad:
        try:
            vlm.parse_answer(b)
            out.append(False)
        except ValueError:
            out.append(True)
    return all(out)


@check
def request_body():
    """the /api/chat body: the image, the schema as format, temperature 0, a fixed seed, no streaming; think is
    false when the model can think and left out when it can't"""
    a = vlm.build_request("m", "prompt", "QUJD", ["completion", "vision", "thinking"])
    b = vlm.build_request("m", "prompt", "QUJD", ["completion", "vision"])
    return (a["format"] == vlm.SCHEMA and a["messages"] == [{"role": "user", "content": "prompt", "images": ["QUJD"]}]
            and a["options"]["temperature"] == 0 and isinstance(a["options"]["seed"], int)
            and a["stream"] is False and a["think"] is False and "think" not in b)


@check
def ask_records_errors_and_thinking():
    """ask(): a usable reply gives an answer; a bad reply or a failed call gives an error, never an answer;
    thinking text the model returns anyway is recorded"""
    good = {"message": {"content": '{"reason": "r", "player_stalled": true, "category": "other_action"}',
                        "thinking": "hmm"}, "eval_count": 20}
    bad = {"message": {"content": '{"reason": "r"'}}

    def fail(url, body=None, timeout=None):
        raise OSError("connection refused")
    r1 = vlm.ask({}, http=lambda url, body=None, timeout=None: good)
    r2 = vlm.ask({}, http=lambda url, body=None, timeout=None: bad)
    r3 = vlm.ask({}, http=fail)
    return (r1["error"] is None and r1["answer"]["category"] == "other_action" and r1["thinking"] == "hmm"
            and r2["answer"] is None and r2["error"].startswith("unusable")
            and r3["answer"] is None and r3["error"].startswith("call failed"))


# --- Contact sheet times ---

@check
def sheet_times_put_one_frame_on_the_peak():
    """8 times in order, inside the clip; the one nearest the peak is moved exactly onto it, the rest are the
    midpoints of 8 equal slices"""
    times, k = vlm.sheet_times(100.0, 116.0, 107.3)
    mids = [100.0 + (i + 0.5) * 2 for i in range(8)]
    return (len(times) == 8 and times == sorted(times) and times[k] == 107.3 and k == 3
            and all(t == m for i, (t, m) in enumerate(zip(times, mids)) if i != k)
            and all(100.0 <= t < 116.0 for t in times))


@check
def sheet_peak_before_the_clip_is_held_inside():
    """a peak step that starts before the clip (by up to 0.5 s) is held at the clip start; at the end, just inside"""
    t1, k1 = vlm.sheet_times(42.3, 57.3, 42.0)
    t2, k2 = vlm.sheet_times(42.3, 57.3, 57.3)
    return k1 == 0 and t1[0] == 42.3 and k2 == 7 and 57.0 < t2[7] < 57.3 and t2 == sorted(t2)


@check
def sheet_times_stay_in_order_for_any_peak():
    """for 500 random clips and peaks, the times stay in strict order and one of them is the peak"""
    rnd = random.Random(7)
    for _ in range(500):
        s = rnd.uniform(0, 1000)
        e = s + rnd.uniform(15, 30)
        p = rnd.uniform(s, e - 0.05)
        times, k = vlm.sheet_times(s, e, p)
        if any(b <= a for a, b in zip(times, times[1:])) or times[k] != p:
            return False
    return True


@check
def sheet_filter_labels_the_peak():
    """the ffmpeg filter has 8 inputs, a 4x2 grid, escaped times, and PEAK (in yellow) on one label only"""
    times, k = vlm.sheet_times(60.0, 75.0, 64.0)
    f = vlm.sheet_filter(times, k, True)
    return (f.count("PEAK") == 1 and f.count("fontcolor=yellow") == 1 and "1\\:04.0   PEAK" in f
            and f.count("hstack=inputs=4") == 2 and "vstack=inputs=2[out]" in f and "in_range=tv" in f
            and "in_range=tv" not in vlm.sheet_filter(times, k, False))


# --- Cache ---

def fields(video, **change):
    c = {"start_s": 10.0, "end_s": 30.0, "peak_time_s": 21.0}
    base = dict(clip=c, model="m", digest="d1", prompt_version="veto_v1", prompt_sha="s1", think=False)
    for k, v in change.items():
        if k in c:
            base["clip"] = dict(c, **{k: v})
        else:
            base[k] = v
    return vlm.cache_fields(video, base["clip"], base["model"], base["digest"], base["prompt_version"],
                            base["prompt_sha"], base["think"])


@check
def cache_key_changes_with_every_part():
    """the cache file changes with the clip window, the peak, the model, its digest, the prompt version or text,
    and the think setting; the same fields give the same file"""
    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "v.webm"
        video.write_bytes(b"12345")
        p0 = vlm.cache_path(tmp, fields(video))
        changed = [fields(video, start_s=10.5), fields(video, end_s=29.0), fields(video, peak_time_s=22.0),
                   fields(video, model="m2"), fields(video, digest="d2"), fields(video, prompt_version="veto_v2"),
                   fields(video, prompt_sha="s2"), fields(video, think=None)]
        other = Path(tmp) / "w.webm"
        other.write_bytes(b"123456")
        paths = {vlm.cache_path(tmp, f) for f in changed + [fields(other)]}
        return vlm.cache_path(tmp, fields(video)) == p0 and p0 not in paths and len(paths) == len(changed) + 1


def fake_candidates(n):
    return [{"start_s": 100.0 * i, "end_s": 100.0 * i + 20, "peak_time_s": 100.0 * i + 10} for i in range(n)]


def run_judge(tmp, video, answers, calls):
    """judge() with a fake sheet maker and a fake model that answers from `answers` (a category, or None for a
    failed call). The fake "sheet" holds the candidate's number, so the fake model knows which one it sees."""
    def sheet_fn(c, path):
        Path(path).write_bytes(str(int(c["start_s"] // 100)).encode("ascii"))
        return vlm.sheet_times(c["start_s"], c["end_s"], c["peak_time_s"])

    def ask_fn(body):
        calls.append(body)
        cat = answers[int(base64.b64decode(body["messages"][0]["images"][0]))]
        if cat is None:
            return {"answer": None, "error": "call failed: made up", "wall_s": 0.1}
        return {"answer": {"reason": "made up", "player_stalled": False, "category": cat}, "error": None,
                "raw": "", "thinking": "", "wall_s": 0.1}
    cands = fake_candidates(len(answers))
    paths = [Path(tmp) / f"s{i}.jpg" for i in range(len(cands))]
    return vlm.judge(cands, video, "m", {"digest": "d1", "capabilities": ["vision"]}, ("prompt", "sha"), paths,
                     Path(tmp) / "cache", sheet_fn, ask_fn, log=lambda *a: None)


@check
def cached_answers_skip_the_model():
    """first run: 10 model calls, answers cached; second run: 0 calls and the same verdicts, marked cached"""
    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "v.webm"
        video.write_bytes(b"x")
        answers = ["chase_or_shootout", "clumsy_crash_or_stuck"] * 5
        calls1, calls2 = [], []
        r1 = run_judge(tmp, video, answers, calls1)
        r2 = run_judge(tmp, video, answers, calls2)
        return (len(calls1) == 10 and len(calls2) == 0 and not any(r["cached"] for r in r1)
                and all(r["cached"] for r in r2)
                and [r["vetoed"] for r in r1] == [r["vetoed"] for r in r2] == [False, True] * 5)


@check
def errors_are_not_cached():
    """a candidate whose call failed is kept (not vetoed) and asked again on the next run; the others are cached"""
    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "v.webm"
        video.write_bytes(b"x")
        calls1, calls2 = [], []
        r1 = run_judge(tmp, video, ["menu_or_cutscene", None, "other_action"], calls1)
        r2 = run_judge(tmp, video, ["menu_or_cutscene", None, "other_action"], calls2)
        return (len(calls1) == 3 and len(calls2) == 1 and r1[1]["error"] and not r1[1]["vetoed"]
                and r1[0]["vetoed"] and r2[0]["cached"] and not r2[1]["cached"])


@check
def cache_file_with_other_fields_is_ignored():
    """a cache file whose stored fields don't match the fields asked for is not used"""
    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "v.webm"
        video.write_bytes(b"x")
        f = fields(video)
        vlm.cache_save(tmp, f, {"answer": None, "error": None})
        path = vlm.cache_path(tmp, f)
        entry = json.loads(path.read_text(encoding="utf-8"))
        entry["fields"]["model"] = "other"
        path.write_text(json.dumps(entry), encoding="utf-8")
        return vlm.cache_load(tmp, f) is None


# --- Ollama housekeeping, with a fake server ---

@check
def unload_waits_until_the_model_is_gone():
    """unload() sends keep_alive 0, then polls /api/ps until the model is no longer listed"""
    sent, polls = [], [0]

    def http(url, body=None, timeout=None):
        if url.endswith("/api/generate"):
            sent.append(body)
            return {}
        polls[0] += 1
        return {"models": [{"name": "m"}] if polls[0] < 3 else []}
    _, gone = vlm.unload("m", http=http, sleep=lambda s: None)
    return gone and sent == [{"model": "m", "keep_alive": 0}] and polls[0] == 3


@check
def missing_model_or_ollama_stops_the_run():
    """check_ollama(): a model that isn't installed, or no Ollama at all, raises VlmError"""
    def no_server(url, body=None, timeout=None):
        raise OSError("refused")

    def no_model(url, body=None, timeout=None):
        return {"models": [{"name": "qwen3-vl:4b", "model": "qwen3-vl:4b", "digest": "x"}]}
    out = []
    for http in (no_server, no_model):
        try:
            vlm.check_ollama("qwen3-vl:8b-instruct", http=http)
            out.append(False)
        except vlm.VlmError:
            out.append(True)
    return all(out)


@check
def the_prompt_file_exists():
    """prompts/<PROMPT_VERSION>.txt exists, mentions every category, and its sha256 is reported"""
    text, sha = vlm.load_prompt()
    return all(c in text for c in vlm.CATEGORIES) and len(sha) == 64


@check
def crlf_checkout_gives_the_same_prompt():
    """a prompt file with CRLF line endings gives the same text (no \\r) and hash as the LF file"""
    old = vlm.PROMPT_DIR
    try:
        with tempfile.TemporaryDirectory() as tmp:
            vlm.PROMPT_DIR = Path(tmp)
            (Path(tmp) / "lf.txt").write_bytes(b"line one\nline two\n")
            (Path(tmp) / "crlf.txt").write_bytes(b"line one\r\nline two\r\n")
            lf, crlf = vlm.load_prompt("lf"), vlm.load_prompt("crlf")
            return lf == crlf and "\r" not in crlf[0]
    finally:
        vlm.PROMPT_DIR = old


# --- Table B (vlmeval.py): veto vs the owner's rating ---

@check
def judge_outcomes():
    """vetoed+postable = costly; kept+not postable; both agreements; unrated; an error counts as kept; a filler as
    vetoed. Ratings are found with eval.py's peak rule"""
    import vlmeval

    def cand(rank, vetoed, filler=False, error=None):
        t = rank * 100.0
        return {"candidate_rank": rank, "start_s": t, "end_s": t + 20, "peak_time_s": t + 10,
                "vlm": {"vetoed": vetoed, "filler": filler, "error": error, "category": "x"}}

    def rating(rank, postable):
        t = rank * 100.0
        return {"start_s": t, "end_s": t + 20, "peak_s": t + 10, "postable": postable, "reason": "made up",
                "file": "f", "rated": "2026-10-24", "run": "t/20261024-120000"}
    scores = {"vlm": {"candidates": [cand(1, True), cand(2, False), cand(3, True), cand(4, False),
                                     cand(5, False), cand(6, True, filler=True), cand(7, True, error="call failed")]}}
    rated = [rating(1, True), rating(2, False), rating(3, False), rating(4, True), rating(6, True), rating(7, False)]
    got = [o for _, _, o in vlmeval.judge_rows(scores, rated)]
    return got == ["vetoed_postable", "kept_not_postable", "agree_vetoed", "agree_kept", "unrated",
                   "vetoed_postable", "kept_not_postable"]


# --- Candidates: the first --count of 2 x --count are the clips the picker makes without --vlm ---

@check
def first_five_of_ten_candidates_are_the_five_picks():
    """on made-up scores (bursts, walls, a phone stretch), pick_clips with count 10 starts with the count-5 picks"""
    rnd = random.Random(3)
    n = 2400
    score = [max(0.0, rnd.gauss(0.6, 0.4)) for _ in range(n)]
    for _ in range(25):
        c = rnd.randrange(n)
        for i in range(c, min(n, c + rnd.randrange(2, 30))):
            score[i] += rnd.uniform(1.5, 5.0)
    walls = [600 <= i < 640 or 1500 <= i < 1520 for i in range(n)]
    phone = [900 <= i < 950 for i in range(n)]
    score = [0.0 if w else s for s, w in zip(score, walls)]
    smooth = clip.gaussian_smooth(score, clip.SMOOTH_SIGMA / clip.BUCKET)
    seq, _ = clip.action_sequences(score, [w or p for w, p in zip(walls, phone)])
    usable = n * clip.BUCKET - clip.END_MARGIN
    five, _, _ = clip.pick_clips(score, smooth, usable, 5, 15.0, 30.0, 10.0, walls, phone, seq)
    ten, _, _ = clip.pick_clips(score, smooth, usable, 10, 15.0, 30.0, 10.0, walls, phone, seq)
    return len(five) == 5 and len(ten) == 10 and ten[:5] == five


# --- v7: measured stars (--vlm-hint stars, --vlm-rule stars) ---

@check
def stars_are_cut_at_the_clip_edges():
    """stars_in_clip counts only the part of each 0.5 s star step inside the clip: 30.0 s for a 30 s clip that
    starts mid-step with stars throughout (stars_s says 30.5), the inside of a partial stretch, 0 with no stars"""
    every = [True] * 1000
    part = [100 <= i < 125 for i in range(1000)]  # stars 50.0-62.5 s
    return (vlm.stars_in_clip(190.25, 220.25, every, 0.5) == 30.0
            and vlm.stars_in_clip(55.3, 70.3, part, 0.5) == 7.2
            and vlm.stars_in_clip(40.0, 52.25, part, 0.5) == 2.25
            and vlm.stars_in_clip(0.1, 15.1, [False] * 1000, 0.5) == 0.0
            and vlm.stars_in_clip(495.0, 510.0, every, 0.5) == 5.0)  # the timeline ends at 500 s


@check
def stars_never_exceed_the_clip():
    """for 500 random clips over random star steps, the measured stars are between 0 and the clip's length"""
    rnd = random.Random(11)
    steps = [rnd.random() < 0.4 for _ in range(4000)]
    for _ in range(500):
        s = round(rnd.uniform(0, 1900), 2)
        e = round(s + rnd.uniform(15, 30), 2)
        if not 0.0 <= vlm.stars_in_clip(s, e, steps, 0.5) <= round(e - s, 2):
            return False
    return True


@check
def stars_line_text():
    """the line is 'Measured by the tool: wanted stars visible for X of Y s.' with one decimal, 0 s included"""
    return (vlm.stars_line(12.5, 15.0) == "Measured by the tool: wanted stars visible for 12.5 of 15.0 s."
            and vlm.stars_line(0.0, 16.5) == "Measured by the tool: wanted stars visible for 0.0 of 16.5 s."
            and vlm.stars_line(30, 30) == "Measured by the tool: wanted stars visible for 30.0 of 30.0 s.")


@check
def fill_prompt_needs_the_placeholder_once():
    """the placeholder is replaced by the line; a prompt with no placeholder, or two, is a ValueError"""
    ph = vlm.HINT_PLACEHOLDER
    out = [vlm.fill_prompt(f"a\n{ph}\nb", "LINE") == "a\nLINE\nb"]
    for bad in ("a b", f"{ph} {ph}"):
        try:
            vlm.fill_prompt(bad, "LINE")
            out.append(False)
        except ValueError:
            out.append(True)
    return all(out)


@check
def the_stars_prompt_file():
    """--vlm-hint stars uses prompts/stars_v2.txt (final): one placeholder, every category, no CR; hint none uses
    veto_v3, which has no placeholder"""
    text, sha = vlm.load_prompt(vlm.prompt_version("stars"))
    v3, _ = vlm.load_prompt(vlm.prompt_version("none"))
    return (vlm.prompt_version("stars") == vlm.STARS_PROMPT_VERSION == "stars_v2"
            and vlm.prompt_version("none") == vlm.PROMPT_VERSION == "veto_v3"
            and text.count(vlm.HINT_PLACEHOLDER) == 1 and vlm.HINT_PLACEHOLDER not in v3
            and all(c in text for c in vlm.CATEGORIES) and "\r" not in text and len(sha) == 64)


@check
def hint_none_cache_key_is_v6s():
    """without a hint the cache fields are exactly v6's (same keys, same values), so v6's cached answers apply"""
    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "v.webm"
        video.write_bytes(b"12345")
        c = {"start_s": 10.0, "end_s": 30.0, "peak_time_s": 21.0}
        v6 = {"video": "v.webm", "video_bytes": 5, "start_s": 10.0, "end_s": 30.0, "peak_time_s": 21.0,
              "model": "m", "model_digest": "d1", "prompt_version": "veto_v3", "prompt_sha256": "s1",
              "sheet_version": vlm.SHEET_VERSION, "options": vlm.OPTIONS, "think": None}
        f = vlm.cache_fields(video, c, "m", "d1", "veto_v3", "s1", None)
        return f == v6 and list(f) == list(v6) and vlm.cache_path(tmp, f) == vlm.cache_path(tmp, v6)


@check
def stars_cache_key_includes_the_hint_text():
    """with --vlm-hint stars the key has the hint and the filled-in line: it differs from the no-hint key, and a
    different line gives a different file; the same line gives the same file"""
    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "v.webm"
        video.write_bytes(b"12345")
        c = {"start_s": 10.0, "end_s": 30.0, "peak_time_s": 21.0}

        def path(*hint):
            return vlm.cache_path(tmp, vlm.cache_fields(video, c, "m", "d1", "stars_v1", "s1", None, *hint))
        a = path("stars", vlm.stars_line(12.5, 20.0))
        return (a == path("stars", vlm.stars_line(12.5, 20.0)) and a != path()
                and a != path("stars", vlm.stars_line(12.0, 20.0)))


def run_judge_hints(tmp, video, hints, calls, text="intro\n" + vlm.HINT_PLACEHOLDER + "\nend"):
    """judge() like run_judge, every answer other_action, with the given hints (None = no hint)."""
    def sheet_fn(c, path):
        Path(path).write_bytes(b"0")
        return vlm.sheet_times(c["start_s"], c["end_s"], c["peak_time_s"])

    def ask_fn(body):
        calls.append(body)
        return {"answer": {"reason": "made up", "player_stalled": False, "category": "other_action"}, "error": None,
                "raw": "", "thinking": "", "wall_s": 0.1}
    cands = fake_candidates(2)
    paths = [Path(tmp) / f"s{i}.jpg" for i in range(2)]
    return vlm.judge(cands, video, "m", {"digest": "d1", "capabilities": ["vision"]}, (text, "sha"), paths,
                     Path(tmp) / "cache", sheet_fn, ask_fn, log=lambda *a: None,
                     version="stars_v1" if hints else "veto_v3", hints=hints)


@check
def judge_sends_each_candidate_its_line():
    """with hints, each body's prompt has that candidate's line in place of the placeholder, the result records
    the line, and a re-run with the same lines is cached; changed lines are asked again"""
    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "v.webm"
        video.write_bytes(b"x")
        lines = [vlm.stars_line(0.0, 20.0), vlm.stars_line(12.5, 20.0)]
        c1, c2, c3 = [], [], []
        r1 = run_judge_hints(tmp, video, lines, c1)
        run_judge_hints(tmp, video, lines, c2)
        run_judge_hints(tmp, video, [lines[0], vlm.stars_line(13.0, 20.0)], c3)
        return ([b["messages"][0]["content"] for b in c1] == [f"intro\n{ln}\nend" for ln in lines]
                and [r["hint_text"] for r in r1] == lines and len(c2) == 0 and len(c3) == 1)


@check
def judge_without_hint_sends_the_prompt_unchanged():
    """with no hint the prompt is sent exactly as loaded (v6's body) and results have no hint_text"""
    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "v.webm"
        video.write_bytes(b"x")
        calls = []
        r = run_judge_hints(tmp, video, None, calls, text="plain v3 text")
        return (len(calls) == 2 and all(b["messages"][0]["content"] == "plain v3 text" for b in calls)
                and not any("hint_text" in x for x in r))


@check
def star_rule_cancels_only_boring_vetoes():
    """--vlm-rule stars: a usable boring_driving_or_walking answer with 5.0 s of stars or more is kept; 4.99 s is
    not; clumsy and menu vetoes stay whatever the stars; an error is not touched"""
    def res(cat, error=None):
        return {"category": cat, "error": error}
    boring = "boring_driving_or_walking"
    return (vlm.STAR_RULE_MIN_S == 5.0 and vlm.star_rule_keeps(res(boring), 5.0)
            and vlm.star_rule_keeps(res(boring), 30.0) and not vlm.star_rule_keeps(res(boring), 4.99)
            and not vlm.star_rule_keeps(res("clumsy_crash_or_stuck"), 30.0)
            and not vlm.star_rule_keeps(res("menu_or_cutscene"), 30.0)
            and not vlm.star_rule_keeps(res(boring, error="call failed"), 30.0))


@check
def stars_options_need_the_hud():
    """star_measure_error: no message with both options off or the HUD on; with the HUD off, a message naming the
    option(s) and the HUD state"""
    off = "off (--no-hud)"
    a = clip.star_measure_error("stars", "none", False, off)
    b = clip.star_measure_error("none", "stars", False, "off (no GTA V minimap found)")
    both = clip.star_measure_error("stars", "stars", False, off)
    return (clip.star_measure_error("none", "none", False, off) is None
            and clip.star_measure_error("stars", "stars", True, "on") is None
            and "--vlm-hint stars needs" in a and off in a
            and "--vlm-rule stars needs" in b and "no GTA V minimap found" in b
            and "--vlm-hint stars and --vlm-rule stars need" in both)


@check
def stars_options_stop_clip_py_at_once():
    """clip.py stops with an error, before ffmpeg or Ollama, for --vlm-hint/--vlm-rule stars without --vlm, and
    for either with --no-hud"""
    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "v.webm"
        video.write_bytes(b"x")
        cases = [(["--vlm-hint", "stars"], "only apply with --vlm"),
                 (["--vlm-rule", "stars"], "only apply with --vlm"),
                 (["--vlm", vlm.MODELS[0], "--vlm-hint", "stars", "--no-hud"], "--vlm-hint stars needs"),
                 (["--vlm", vlm.MODELS[0], "--vlm-rule", "stars", "--no-hud"], "--vlm-rule stars needs")]
        for args, msg in cases:
            p = subprocess.run([sys.executable, str(Path(clip.__file__)), str(video)] + args,
                               capture_output=True, text=True, timeout=60)
            if p.returncode != 1 or msg not in p.stderr:
                return False
    return True


def run_veto_step(tmp, hint, rule, answers, star_steps):
    """clip.veto_step with fake sheets, a fake model (answers: one category per candidate) and a temp cache."""
    saved = {k: getattr(vlm, k) for k in ("make_sheet", "ask", "unload", "model_memory", "DEFAULT_CACHE")}

    def make_sheet(ffmpeg, video, c, out, range_fix):
        Path(out).write_bytes(str(int(c["start_s"] // 100)).encode("ascii"))
        times, k = vlm.sheet_times(c["start_s"], c["end_s"], c["peak_time_s"])
        return times, k

    def ask(body):
        cat = answers[int(base64.b64decode(body["messages"][0]["images"][0]))]
        return {"answer": {"reason": "made up", "player_stalled": False, "category": cat}, "error": None,
                "raw": "", "thinking": "", "wall_s": 0.1}
    try:
        vlm.make_sheet, vlm.ask, vlm.unload = make_sheet, ask, lambda model: (0.0, True)
        vlm.model_memory, vlm.DEFAULT_CACHE = (lambda model: None), Path(tmp) / "cache"
        video = Path(tmp) / "v.webm"
        video.write_bytes(b"x")
        cands = [{"start_s": 100.0 * i, "end_s": 100.0 * i + 20, "duration_s": 20.0, "peak_time_s": 100.0 * i + 10,
                  "peak_score": 3.0, "smoothed_peak_score": 2.5, "sequence": i + 1, "reason": "made up"}
                 for i in range(len(answers))]
        run_dir = Path(tmp) / "run"
        run_dir.mkdir(exist_ok=True)
        info = {"digest": "d1", "capabilities": ["vision"]}
        with contextlib.redirect_stdout(io.StringIO()):
            return clip.veto_step(None, video, "m", info, cands, 2, run_dir, False, hint, rule, star_steps)
    finally:
        for k, v in saved.items():
            setattr(vlm, k, v)


@check
def veto_step_star_rule_and_v6_shape():
    """veto_step: with --vlm-rule stars a boring veto with 5+ s of stars is cancelled ("kept by the star rule"),
    one with less stays vetoed, a clumsy one stays vetoed; with both options off the records and the vlm block have
    no v7 keys (v6's shape)"""
    boring, clumsy = "boring_driving_or_walking", "clumsy_crash_or_stuck"
    answers = [boring, boring, clumsy, "other_action"]
    steps = [10 <= i < 19 or 200 <= i < 240 or 400 <= i < 440 for i in range(800)]  # 4.5 s, 20 s, 20 s, 0 s
    with tempfile.TemporaryDirectory() as tmp:
        clips, block, _ = run_veto_step(tmp, "none", "stars", answers, steps)
    st = [c["vlm"]["status"] for c in block["candidates"]]
    with tempfile.TemporaryDirectory() as tmp:
        clips6, block6, _ = run_veto_step(tmp, "none", "none", answers, None)
    v7_keys = {"stars_in_clip_s", "hint_text", "star_rule_kept"}
    return (st == ["vetoed", "kept by the star rule", "vetoed", "kept"]
            and [c["vlm"]["stars_in_clip_s"] for c in block["candidates"]] == [4.5, 20.0, 20.0, 0.0]
            and [c["candidate_rank"] for c in clips] == [2, 4]
            and block["star_rule"] == {"category": boring, "min_stars_s": 5.0, "vetoes_cancelled": 1}
            and block["prompt_version"] == "veto_v3" and "hint" not in block
            and [c["candidate_rank"] for c in clips6] == [4, 1]
            and not any(v7_keys & set(c["vlm"]) for c in block6["candidates"] + clips6)
            and "star_rule" not in block6 and "hint" not in block6)


@check
def veto_step_hint_lines():
    """veto_step with --vlm-hint stars: prompt stars_v2, each candidate's line from its measured stars and length,
    recorded per candidate, and the vlm block marked with the hint"""
    steps = [200 <= i < 225 for i in range(800)]  # 12.5 s of stars inside candidate 2 (100-120 s)
    with tempfile.TemporaryDirectory() as tmp:
        clips, block, _ = run_veto_step(tmp, "stars", "none", ["other_action"] * 3, steps)
    return (block["prompt_version"] == "stars_v2" and block["hint"] == "stars" and "star_rule" not in block
            and [c["vlm"]["hint_text"] for c in block["candidates"]] == [vlm.stars_line(0.0, 20.0),
                                                                        vlm.stars_line(12.5, 20.0),
                                                                        vlm.stars_line(0.0, 20.0)]
            and clips[1]["vlm"]["hint_text"] == vlm.stars_line(12.5, 20.0))


def main():
    ok = True
    for fn in CHECKS:
        try:
            good, err = bool(fn()), ""
        except Exception as e:  # a missing function or field is a failure of this check, not of the run
            good, err = False, f"  ({type(e).__name__}: {e})"
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'} {fn.__name__}: {fn.__doc__}{err}")
    print("RESULT: " + ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
