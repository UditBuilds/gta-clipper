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
