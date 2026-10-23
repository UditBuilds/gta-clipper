"""Score one gta-clipper run against a hand-made answer key.

An answer key (eval/answer_keys/<video>.json) lists action ranges a viewer would want clipped, and
never-pick ranges (pause/map screens, cutscenes, phone calls, menus), in source seconds. A clip "hits" a
range if they share more than 0 s (clip start < range end and range start < clip end): ranges are half-open,
so a clip that ends exactly where a range starts does not hit it. Every range must have end > start; a range
with start == end could never be hit, so eval.py stops with an error on one.

- overlap precision: picked clips that hit at least one action range, out of the clips actually picked
- strict precision:  picked clips with at least STRICT_SHARE (50 %) of their length inside action ranges
                     (inside the union of the ranges, so overlapping ranges are not counted twice)
- recall:    action ranges hit by at least one clip, out of all action ranges (each range counts once;
             more than 0 s shared counts, as for overlap precision)
- bad picks: clips that hit any never-pick range (more than 0 s shared)
- postable:  clips the owner rated postable, out of the clips picked, from eval/ratings/<video>_*.json.
             A rating belongs to a clip time range, not a clip number. A clip reuses a rating only if the
             two ranges share at least RATING_MATCH (80 %) of the LONGER one's length, i.e. each covers 80 %
             of the other; otherwise it is "unrated". "Bad cut" ratings are about where the clip starts and
             ends, so a 20 s clip inside a rated 30 s clip stays unrated. If several ratings match, the one
             with the most shared time wins.

Per-clip overlap seconds are printed too, so a clip that only grazes a range by a second stands out.
The last line starts with SUMMARY and is meant to be parsed.

Usage:  python eval.py <run_dir> [answer_key.json]
  The key defaults to eval/answer_keys/<video name>.json next to this script.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEY_DIR = HERE / "eval" / "answer_keys"
RATINGS_DIR = HERE / "eval" / "ratings"
DURATION_TOL = 1.0  # seconds; warn if the run's input and the key disagree by more
STRICT_SHARE = 0.5  # share of a clip's length that must be inside action ranges for a strict hit
RATING_MATCH = 0.8  # shared time, as a share of the longer clip, for a rating to carry over


def hms(seconds):
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:04.1f}"


def span(r):
    if r["start_s"] == r["end_s"]:
        return hms(r["start_s"])
    return f"{hms(r['start_s'])}-{hms(r['end_s'])}"


def hits(clip, ranges):
    """(index, overlap seconds) for every range the clip shares more than 0 s with."""
    s, e = clip["start_s"], clip["end_s"]
    shared = ((i, min(e, r["end_s"]) - max(s, r["start_s"])) for i, r in enumerate(ranges))
    return [(i, ov) for i, ov in shared if ov > 0]


def key_errors(key):
    """One message for each range in the key that doesn't run forwards (end must be > start)."""
    return [f"{kind} range {i + 1} ({r['start_s']}-{r['end_s']}): end must be after start"
            for kind in ("action", "never_pick") for i, r in enumerate(key[kind]) if not r["end_s"] > r["start_s"]]


def inside(clip, ranges):
    """Seconds of the clip that lie inside the union of the ranges."""
    merged = []
    for r in sorted(ranges, key=lambda r: r["start_s"]):
        if merged and r["start_s"] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], r["end_s"])
        else:
            merged.append([r["start_s"], r["end_s"]])
    s, e = clip["start_s"], clip["end_s"]
    return sum(max(0.0, min(e, b) - max(s, a)) for a, b in merged)


def load_ratings(stem):
    """Every rated clip for this video, from all eval/ratings/<video>_*.json files."""
    rated = []
    for path in sorted(RATINGS_DIR.glob(f"{stem}_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        rated += [dict(r, file=path.stem) for r in data["clips"]]
    return rated


def rating_for(clip, rated):
    """(rating, shared seconds) of the best matching rated clip, or (None, 0.0) if none shares RATING_MATCH."""
    s, e = clip["start_s"], clip["end_s"]
    best, best_ov = None, 0.0
    for r in rated:
        ov = max(0.0, min(e, r["end_s"]) - max(s, r["start_s"]))
        if ov >= RATING_MATCH * max(e - s, r["end_s"] - r["start_s"]) and ov > best_ov:
            best, best_ov = r, ov
    return best, best_ov


def evaluate(scores, key, rated):
    clips = sorted(scores["clips"], key=lambda c: c["rank"])
    action, never = key["action"], key["never_pick"]
    rows, ranges_hit = [], set()
    for c in clips:
        a, n = hits(c, action), hits(c, never)
        ranges_hit.update(i for i, _ in a)
        length = c["end_s"] - c["start_s"]
        ins = inside(c, action)
        rating, rating_ov = rating_for(c, rated)
        rows.append({"clip": c, "action": a, "never": n, "inside_s": ins,
                     "strict": length > 0 and ins >= STRICT_SHARE * length,
                     "rating": rating, "rating_overlap_s": rating_ov})
    return {
        "rows": rows,
        "n_clips": len(clips),
        "n_action_clips": sum(1 for r in rows if r["action"]),
        "n_strict": sum(1 for r in rows if r["strict"]),
        "n_ranges": len(action),
        "ranges_hit": ranges_hit,
        "n_bad": sum(1 for r in rows if r["never"]),
        "n_postable": sum(1 for r in rows if r["rating"] and r["rating"]["postable"]),
        "n_unrated": sum(1 for r in rows if not r["rating"]),
    }


def ratio(k, n):
    return f"{k}/{n} ({k / n:.0%})" if n else f"{k}/{n} (n/a)"


def main():
    ap = argparse.ArgumentParser(description="Precision, recall, bad picks and owner ratings of a clip.py run "
                                             "vs an answer key.")
    ap.add_argument("run_dir", type=Path, help="run folder containing scores.json")
    ap.add_argument("key", type=Path, nargs="?", help="answer key (default: eval/answer_keys/<video>.json)")
    args = ap.parse_args()

    scores_path = args.run_dir / "scores.json"
    if not scores_path.is_file():
        sys.exit(f"ERROR: no scores.json in {args.run_dir}")
    scores = json.loads(scores_path.read_text(encoding="utf-8"))
    video = Path(scores["input"]).name
    key_path = args.key or KEY_DIR / (Path(video).stem + ".json")
    if not key_path.is_file():
        sys.exit(f"ERROR: answer key not found: {key_path}")
    key = json.loads(key_path.read_text(encoding="utf-8"))
    errors = key_errors(key)
    if errors:
        sys.exit(f"ERROR: {key_path}: " + "; ".join(errors))

    if Path(key["video"]).stem != Path(video).stem:
        print(f"WARNING: run is for {video} but the key is for {key['video']}")
    dur = scores["input_info"]["duration"]
    if abs(dur - key["duration_s"]) > DURATION_TOL:
        print(f"WARNING: run input is {dur:.1f} s long but the key says {key['duration_s']:.1f} s")

    rated = load_ratings(Path(video).stem)
    res = evaluate(scores, key, rated)
    action, never = key["action"], key["never_pick"]
    print(f"Run:  {args.run_dir}  ({scores.get('tool', '?')}, {video})")
    print(f"Key:  {key_path}  (confirmed {key.get('confirmed', '?')}; "
          f"{len(action)} action ranges, {len(never)} never-pick ranges)")
    files = sorted({r["file"] for r in rated})
    print(f"Ratings: {len(rated)} rated clip(s) from {', '.join(files) if files else 'no ratings file'} "
          f"in {RATINGS_DIR}")
    print("\nClips:")
    print(f"  {'#':>3}  {'clip':<17} {'in action':>9}  {'hit':<7} {'bad':<4} postable")
    for r in res["rows"]:
        c = r["clip"]
        length = c["end_s"] - c["start_s"]
        hit = ("strict" if r["strict"] else "overlap") if r["action"] else "-"
        post = "unrated" if not r["rating"] else ("yes" if r["rating"]["postable"] else "no")
        share = f"{r['inside_s'] / length:.0%}" if length > 0 else "n/a"
        print(f"  [{c['rank']}]  {hms(c['start_s']) + '-' + hms(c['end_s']):<17} {share:>9}  {hit:<7} "
              f"{'BAD' if r['never'] else '-':<4} {post}")
    print(f"  (in action = share of the clip inside action ranges; strict needs {STRICT_SHARE:.0%})")
    print("\nClip details:")
    for r in res["rows"]:
        c = r["clip"]
        a = ", ".join(f"A{i + 1} {span(action[i])} ({ov:.1f} s)" for i, ov in r["action"]) or "-"
        n = ", ".join(f"N{i + 1} {never[i]['kind']} {span(never[i])} ({ov:.1f} s)" for i, ov in r["never"])
        print(f"  [{c['rank']}] {hms(c['start_s'])}-{hms(c['end_s'])}")
        print(f"      action: {a}  ({r['inside_s']:.1f} s inside)")
        if n:
            print(f"      never-pick: {n}")
        g = r["rating"]
        if g:
            print(f"      rating: {'postable' if g['postable'] else 'not postable'} - {g['reason']} "
                  f"[{g['file']}, rated clip {hms(g['start_s'])}-{hms(g['end_s'])}, "
                  f"{r['rating_overlap_s']:.1f} s shared]")
        else:
            print(f"      rating: unrated (no rated clip shares {RATING_MATCH:.0%} of the longer clip)")
    print("\nAction ranges:")
    for i, rng in enumerate(action):
        by = [r["clip"]["rank"] for r in res["rows"] if any(j == i for j, _ in r["action"])]
        got = "clip " + ", ".join(map(str, by)) if by else "MISSED"
        print(f"  A{i + 1} {span(rng):>15}  {got:<12} {rng['note']}")

    print(f"\nOverlap precision: {ratio(res['n_action_clips'], res['n_clips'])} picked clips touch an action range")
    print(f"Strict precision:  {ratio(res['n_strict'], res['n_clips'])} picked clips are at least "
          f"{STRICT_SHARE:.0%} inside action ranges")
    print(f"Recall:    {ratio(len(res['ranges_hit']), res['n_ranges'])} action ranges got a clip (more than 0 s shared)")
    print(f"Bad picks: {res['n_bad']} clip(s) hit a never-pick range")
    print(f"Postable:  {res['n_postable']}/{res['n_clips']} ({res['n_unrated']} unrated)")
    total = scores.get("timing_s", {}).get("total")
    print(f"SUMMARY video={Path(video).stem} run={args.run_dir.name} tool=\"{scores.get('tool', '?')}\" "
          f"clips={res['n_clips']} precision={res['n_action_clips']}/{res['n_clips']} "
          f"strict={res['n_strict']}/{res['n_clips']} "
          f"recall={len(res['ranges_hit'])}/{res['n_ranges']} bad={res['n_bad']} "
          f"postable={res['n_postable']}/{res['n_clips']} unrated={res['n_unrated']} "
          f"runtime_s={total if total is not None else 'na'}")


if __name__ == "__main__":
    main()
