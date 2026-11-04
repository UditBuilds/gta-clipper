"""Unit checks for --spread (what ends an action sequence) and for make_rating_page.py's choice of clips, on made-up
scores and clips. No footage, ffmpeg or run folders are needed.

Each check prints PASS or FAIL with what it tests. A check that raises counts as FAIL (with the error). The last line
is RESULT: PASS or FAIL; exit code 0 or 1.

Usage:  python spreadtest.py
"""

import random
import sys

import clip
import make_rating_page as page

CHECKS = []
STEPS_PER_S = round(1 / clip.BUCKET)


def check(fn):
    CHECKS.append(fn)
    return fn


def steps(seconds):
    return round(seconds * STEPS_PER_S)


def timeline(total_s, active=(), hidden=()):
    """(combined, excluded) as clip.py builds them: active stretches score 4.0, hidden stretches score 0 and are excluded."""
    n = steps(total_s)
    excluded = [any(steps(a) <= i < steps(b) for a, b in hidden) for i in range(n)]
    combined = [4.0 if not excluded[i] and any(steps(a) <= i < steps(b) for a, b in active) else 0.0 for i in range(n)]
    return combined, excluded


def spans(combined, excluded, spread):
    return clip.action_sequences(combined, excluded, spread)[1]


# --- What ends an action sequence ---

@check
def short_wall_splits_v9_not_v10():
    """the 4 Nov shape: a chase cut by a 10 s stretch with no minimap is 2 sequences in v9 and 1 in v10"""
    combined, excluded = timeline(200, active=[(10, 60), (70, 120)], hidden=[(60, 70)])
    return (spans(combined, excluded, "v9") == [(10.0, 60.0), (70.0, 120.0)]
            and spans(combined, excluded, "v10") == [(10.0, 120.0)])


@check
def long_wall_ends_a_sequence_in_both():
    """a 40 s stretch with no minimap (a cutscene) ends the sequence in v9 and in v10"""
    combined, excluded = timeline(200, active=[(10, 60), (100, 120)], hidden=[(60, 100)])
    want = [(10.0, 60.0), (100.0, 120.0)]
    return spans(combined, excluded, "v9") == want and spans(combined, excluded, "v10") == want


@check
def wall_counts_as_quiet_in_v10():
    """v10: 15 s hidden + 6 s calm is 21 s of quiet, which ends a sequence; 15 s hidden + 4 s calm is 19 s, which doesn't"""
    ended = timeline(200, active=[(10, 60), (81, 100)], hidden=[(60, 75)])
    kept = timeline(200, active=[(10, 60), (79, 100)], hidden=[(60, 75)])
    return (spans(*ended, "v10") == [(10.0, 60.0), (81.0, 100.0)]
            and spans(*kept, "v10") == [(10.0, 100.0)])


@check
def without_walls_v9_and_v10_are_the_same():
    """on 50 random timelines with nothing excluded, v9 and v10 give the same sequences"""
    rnd = random.Random(5)
    for _ in range(50):
        combined = [rnd.choice((0.0, 0.0, 0.0, 1.0, 3.0)) if rnd.random() < 0.02 else 0.0 for _ in range(2000)]
        excluded = [False] * 2000
        if clip.action_sequences(combined, excluded, "v9") != clip.action_sequences(combined, excluded, "v10"):
            return False
    return True


@check
def excluded_steps_have_no_sequence():
    """in v10 too, a step inside a stretch with no minimap belongs to no sequence, even when its score is high"""
    combined, excluded = timeline(100, active=[(10, 30), (35, 50)], hidden=[(30, 35)])
    combined[steps(32)] = 5.0
    seq, _ = clip.action_sequences(combined, excluded, "v10")
    return seq[steps(32)] is None and seq[steps(20)] == seq[steps(40)] == 0


# --- What that does to the picks ---

def picks(spread, count=5):
    """A long chase cut by two short hides, then three separate bursts."""
    combined, excluded = timeline(1000, active=[(100, 220), (230, 350), (360, 480), (600, 620), (700, 720), (800, 820)],
                                  hidden=[(220, 230), (350, 360)])
    # The chase scores higher than the bursts, as a chase with wanted stars does.
    combined = [c + 2.0 if steps(100) <= i < steps(480) and c else c for i, c in enumerate(combined)]
    smooth = clip.gaussian_smooth(combined, clip.SMOOTH_SIGMA / clip.BUCKET)
    seq, _ = clip.action_sequences(combined, excluded, spread)
    taken, _, _ = clip.pick_clips(combined, smooth, 1000 - clip.END_MARGIN, count, 15.0, 30.0, 10.0, excluded,
                                  [False] * len(combined), seq)
    return taken


@check
def v9_lets_one_chase_take_every_pick():
    """the 4 Nov failure, on made-up scores: with v9 all 5 picks come from the chase cut by short hides"""
    return all(c["end_s"] <= 480 for c in picks("v9"))


@check
def v10_caps_the_chase_and_spreads():
    """with v10 the same chase gives 2 picks and the other 3 go to the separate bursts"""
    chase = [c for c in picks("v10") if c["end_s"] <= 480]
    return len(chase) == 2 and len(picks("v10")) == 5


@check
def first_five_of_ten_are_the_five_picks_in_v10():
    """the --vlm candidates rely on this: with v10 too, count 10 starts with the count-5 picks"""
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
    seq, _ = clip.action_sequences(score, [w or p for w, p in zip(walls, phone)], "v10")
    usable = n * clip.BUCKET - clip.END_MARGIN
    five, _, _ = clip.pick_clips(score, smooth, usable, 5, 15.0, 30.0, 10.0, walls, phone, seq)
    ten, _, _ = clip.pick_clips(score, smooth, usable, 10, 15.0, 30.0, 10.0, walls, phone, seq)
    return len(five) == 5 and len(ten) == 10 and ten[:5] == five


# --- make_rating_page.py: which clips get rated ---

def run_of(video, *clips):
    """(run_dir, scores) with clips given as (start, end)."""
    return f"run_{video}_{clips[0][0]}", {"input": f"C:\\x\\{video}.webm", "clips": [
        {"start_s": s, "end_s": e, "peak_time_s": s + 5, "file": f"clip_{s}.mp4"} for s, e in clips]}


def rating(start, end):
    return {"start_s": start, "end_s": end, "peak_s": start + 5, "postable": True, "rated": "2026-11-04", "run": "x/1"}


@check
def a_clip_in_two_runs_comes_once():
    """the same clip in the old and the new run is rated once; the other clips stay, in source-time order"""
    got = page.clips_to_rate([run_of("v", (100, 120), (300, 320)), run_of("v", (100, 120), (50, 70))], False)
    return [c["start_s"] for _, c, _ in got] == [50, 100, 300]


@check
def unrated_only_leaves_out_rated_clips():
    """--unrated-only drops a clip that has a rating by eval.py's rule, here another cut with a peak 2 s away"""
    rated = {"v": [rating(102, 125)]}
    got = page.clips_to_rate([run_of("v", (100, 120), (300, 320))], True, lambda video: rated.get(video, []))
    return [c["start_s"] for _, c, _ in got] == [300]


@check
def every_clip_stays_without_unrated_only():
    """without --unrated-only a rated clip is still on the page, as for the held-out test"""
    got = page.clips_to_rate([run_of("v", (100, 120))], False, lambda video: [rating(100, 120)])
    return len(got) == 1


@check
def videos_stay_apart():
    """the same times in two recordings are two clips, grouped by recording"""
    got = page.clips_to_rate([run_of("b", (100, 120)), run_of("a", (100, 120))], False)
    return [v for v, _, _ in got] == ["a", "b"]


@check
def page_shows_nothing_about_the_pick():
    """the page template holds no score, peak, rank, run or tool wording"""
    t = page.TEMPLATE.lower()
    return not any(w in t for w in ("score", "peak", "rank", "gta-clipper", "spread", "scores.json"))


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
