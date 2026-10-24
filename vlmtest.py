"""Unit checks for the v6 veto (vlm.py) on made-up model answers. No Ollama, no footage and no ffmpeg are used.

Each check prints PASS or FAIL with what it tests. A check that raises counts as FAIL (with the error), so a
missing function shows as a failure, not a crash. The last line is RESULT: PASS or FAIL; exit code 0 or 1.

Usage:  python vlmtest.py
"""

import base64
import json
import random
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
