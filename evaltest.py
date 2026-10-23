"""Unit checks for eval.py on made-up clips, ranges and ratings. No footage and no run folders are read.

Each check prints PASS or FAIL with what it tests. A check that raises counts as FAIL (with the error), so a
missing function shows as a failure, not a crash. The last line is RESULT: PASS or FAIL; exit code 0 or 1.

Usage:  python evaltest.py
"""

import sys

import eval as ev

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def clip(start, end, rank=1, peak=None):
    """A picked clip as scores.json has it. The peak defaults to the middle of the clip."""
    return {"start_s": start, "end_s": end, "rank": rank,
            "peak_time_s": (start + end) / 2 if peak is None else peak}


def rng(start, end, kind="cutscene"):
    return {"start_s": start, "end_s": end, "kind": kind, "note": ""}


def key(action=(), never=()):
    return {"action": list(action), "never_pick": list(never)}


# --- Intervals (claim 1): a clip hits a range only if they share more than 0 s ---

@check
def touch_at_clip_end_is_not_a_hit():
    """test1 v4 clip 3 ends at 4:19.0 where the cutscene range starts (0.0 s shared): no hit, no bad pick"""
    res = ev.evaluate({"clips": [clip(230.25, 259.0)]}, key(never=[rng(259, 278)]), [])
    return ev.hits(clip(230.25, 259.0), [rng(259, 278)]) == [] and res["n_bad"] == 0


@check
def touch_at_clip_start_is_not_a_hit():
    """a clip that starts where a range ends (0.0 s shared) does not hit it"""
    return ev.hits(clip(278, 300), [rng(259, 278)]) == []


@check
def one_second_overlap_is_a_hit():
    """a clip that runs 1 s into a range hits it, with 1.0 s shared"""
    return ev.hits(clip(250, 260), [rng(259, 278)]) == [(0, 1.0)]


@check
def clip_inside_range_is_a_hit():
    """a clip inside a range hits it with its whole length"""
    return ev.hits(clip(260, 270), [rng(259, 278)]) == [(0, 10.0)]


@check
def touch_does_not_count_for_recall():
    """an action range that a clip only touches (0.0 s) is not recalled and the clip is not an overlap hit"""
    res = ev.evaluate({"clips": [clip(80, 100)]}, key(action=[rng(100, 120, "action")]), [])
    return res["ranges_hit"] == set() and res["n_action_clips"] == 0


@check
def zero_length_range_is_an_error():
    """a range with start == end (it could never be hit) is reported as an error; end < start too"""
    good = key(action=[rng(5, 10, "action")], never=[rng(20, 30)])
    return (ev.key_errors(good) == [] and len(ev.key_errors(key(action=[rng(5, 5, "action")]))) == 1
            and len(ev.key_errors(key(never=[rng(30, 20)]))) == 1)


# --- Ratings (claim 8): a rating belongs to a peak, not to a cut ---

def rating(start, end, peak, postable=True, rated="2026-10-22", run="test1/20261022-120000", file="test1_vx"):
    """A rated clip as load_ratings() returns it."""
    return {"start_s": start, "end_s": end, "peak_s": peak, "postable": postable, "reason": "made up",
            "file": file, "rated": rated, "run": run}


@check
def shorter_cut_of_same_peak_takes_the_rating():
    """a 15 s clip with the same peak as a rated 30 s clip takes its rating (the 80 % rule left it unrated)"""
    r = rating(183.5, 213.5, 204.5)
    return ev.rating_for(clip(190.25, 205.25, peak=204.5), [r])[0] is r


@check
def peaks_4_s_apart_match_4_5_s_do_not():
    """peaks 4.0 s apart: the rating applies; 4.5 s apart: unrated (clips 1 s apart, so not the same clip)"""
    near, far = rating(90, 110, 104.0), rating(90, 110, 104.5)
    return (ev.rating_for(clip(91, 110, peak=100), [near])[0] is near
            and ev.rating_for(clip(91, 110, peak=100), [far])[0] is None)


@check
def rated_clips_under_15_s_do_not_count():
    """a rating of a 14.9 s clip doesn't count, even for a picked clip that covers it with the same peak"""
    return ev.rating_for(clip(100, 115, peak=105), [rating(100, 114.9, 105)])[0] is None


@check
def picked_clip_must_be_15_to_30_s():
    """the picked clip must be 15-30 s long: 15 and 30 s take a rating, 31 s does not"""
    r = rating(90, 120, 105)
    return (ev.rating_for(clip(98, 113, peak=105), [r])[0] is r and ev.rating_for(clip(90, 120, peak=105), [r])[0] is r
            and ev.rating_for(clip(90, 121, peak=105), [r])[0] is None)


@check
def newest_rating_wins():
    """of several ratings within 4 s: latest rated date, then latest run, then nearest peak"""
    old = rating(100, 120, 110, postable=True, rated="2026-10-22", run="t/20261022-120000")
    new = rating(105, 120, 112, postable=False, rated="2026-10-23", run="t/20261023-100000")
    later_run = rating(100, 120, 113, postable=True, rated="2026-10-23", run="t/20261023-200000")
    nearer = rating(101, 121, 110.5, postable=False, rated="2026-10-23", run="t/20261023-200000")
    c = clip(102, 118, peak=110)  # not the same clip as any of them
    return (ev.rating_for(c, [old, new])[0] is new and ev.rating_for(c, [new, old])[0] is new
            and ev.rating_for(c, [old, new, later_run])[0] is later_run
            and ev.rating_for(c, [later_run, nearer])[0] is nearer)


@check
def same_clip_rating_beats_a_newer_peak_match():
    """a rating of the exact same clip (start and end within 0.01 s) beats a newer rating matched by peak"""
    own = rating(146.0, 166.0, 155.0, postable=False, rated="2026-10-22", run="t/20261022-120429")
    other_cut = rating(136.0, 166.0, 155.0, postable=True, rated="2026-10-23", run="t/20261023-205413")
    c = clip(146.0, 166.0, peak=155.0)
    return ev.rating_for(c, [own, other_cut]) == (own, 20.0) and ev.rating_for(c, [other_cut, own])[0] is own


@check
def same_clip_rating_applies_even_if_peaks_drift():
    """a rating of the exact same clip applies even when the peaks are over 4 s apart; the length rules still hold"""
    r, short = rating(100.0, 120.0, 101.0), rating(100.0, 110.0, 101.0)
    return (ev.rating_for(clip(100.0, 120.0, peak=118.0), [r])[0] is r
            and ev.rating_for(clip(100.0, 110.0, peak=101.0), [short])[0] is None)


@check
def test1_v3_clip_4_keeps_its_own_rating():
    """test1 v3 2:26.0-2:46.0 keeps its own v2 rating (not postable), not v4 2:16.0-2:46.0's newer one (same peak)"""
    g, _ = ev.rating_for(clip(146.0, 166.0, peak=155.0), ev.load_ratings("test1"))
    return g is not None and g["file"] == "test1_v2" and not g["postable"]


@check
def rating_without_a_peak_is_an_error():
    """a rated clip gets its peak from its own peak_s or from rating_peaks.json; with neither, eval stops"""
    data = {"rated": "2026-10-23", "run": "test1/20261023-205413",
            "clips": [{"start_s": 10.0, "end_s": 30.0, "postable": True, "reason": "made up"}]}
    from_file = ev.with_peaks("x", data, {"x": [{"start_s": 10.0, "end_s": 30.0, "peak_s": 21.0}]})
    own = ev.with_peaks("x", dict(data, clips=[dict(data["clips"][0], peak_s=22.0)]), {})
    try:
        ev.with_peaks("x", data, {})
        stopped = False
    except SystemExit:
        stopped = True
    return from_file[0]["peak_s"] == 21.0 and own[0]["peak_s"] == 22.0 and stopped


@check
def every_rating_in_the_repo_has_a_peak():
    """every rated clip in eval/ratings has a peak (9 for test1, 13 for test2), from its file or rating_peaks.json"""
    t1, t2 = ev.load_ratings("test1"), ev.load_ratings("test2")
    return (len(t1), len(t2)) == (9, 13) and all(isinstance(r["peak_s"], float) for r in t1 + t2)


@check
def owner_rating_of_test1_v4_clip_2_wins():
    """test1 v4 clip 2 (0:57.5-1:27.5, peak 1:08.5) takes its own 2026-10-23 rating, not v2's 0:48-1:08 (peak 1:06.5)"""
    g, _ = ev.rating_for(clip(57.5, 87.5, peak=68.5), ev.load_ratings("test1"))
    return g is not None and g["file"] == "test1_v4_15s_b" and g["postable"]


# --- Postable@5: the first 5 picks by rank ---

def run_of(ranks_and_ratings):
    """scores.json clips (deliberately not in rank order) and their ratings: [(rank, True/False/None)], None = unrated."""
    clips, rated = [], []
    for rank, postable in ranks_and_ratings:
        t = rank * 100.0
        clips.append(clip(t, t + 20, rank=rank, peak=t + 10))
        if postable is not None:
            rated.append(rating(t, t + 20, t + 10, postable=postable))
    return {"clips": clips[::-1]}, rated


@check
def postable_at5_counts_the_first_five_ranks():
    """6 picks: ranks 1-3 postable, 4 not, 5 unrated, 6 postable -> Postable@5 3/5, 1 unrated; all picks 4/6"""
    scores, rated = run_of([(1, True), (2, True), (3, True), (4, False), (5, None), (6, True)])
    res = ev.evaluate(scores, key(), rated)
    return (ev.TOP_K == 5 and (res["postable_at_k"], res["unrated_at_k"]) == (3, 1)
            and (res["n_postable"], res["n_unrated"], res["n_clips"]) == (4, 1, 6))


@check
def fewer_than_five_picks_count_out_of_five():
    """3 picks, all postable: Postable@5 is 3 (of 5, since a missing pick is not postable), 0 unrated"""
    scores, rated = run_of([(1, True), (2, True), (3, True)])
    res = ev.evaluate(scores, key(), rated)
    return (res["postable_at_k"], res["unrated_at_k"]) == (3, 0)


@check
def unrated_is_never_postable():
    """5 unrated picks: Postable@5 0, unrated 5"""
    scores, rated = run_of([(r, None) for r in range(1, 6)])
    res = ev.evaluate(scores, key(), rated)
    return (res["postable_at_k"], res["unrated_at_k"]) == (0, 5)


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
