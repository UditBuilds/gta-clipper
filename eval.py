"""Score one gta-clipper run against a hand-made answer key.

An answer key (eval/answer_keys/<video>.json) lists action ranges a viewer would want clipped, and
never-pick ranges (pause/map screens, cutscenes, phone calls, menus). Ranges are closed intervals in
source seconds; a range with start == end is a single moment. A clip "hits" a range if they share
any time at all (clip start <= range end and range start <= clip end).

- precision: picked clips that hit at least one action range, out of the clips actually picked
- recall:    action ranges hit by at least one clip, out of all action ranges (each range counts once)
- bad picks: clips that hit any never-pick range

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
DURATION_TOL = 1.0  # seconds; warn if the run's input and the key disagree by more


def hms(seconds):
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:04.1f}"


def span(r):
    if r["start_s"] == r["end_s"]:
        return hms(r["start_s"])
    return f"{hms(r['start_s'])}-{hms(r['end_s'])}"


def hits(clip, ranges):
    """(index, overlap seconds) for every range the clip shares time with."""
    s, e = clip["start_s"], clip["end_s"]
    return [(i, max(0.0, min(e, r["end_s"]) - max(s, r["start_s"])))
            for i, r in enumerate(ranges) if s <= r["end_s"] and r["start_s"] <= e]


def evaluate(scores, key):
    clips = sorted(scores["clips"], key=lambda c: c["rank"])
    action, never = key["action"], key["never_pick"]
    rows, ranges_hit = [], set()
    for c in clips:
        a, n = hits(c, action), hits(c, never)
        ranges_hit.update(i for i, _ in a)
        rows.append({"clip": c, "action": a, "never": n})
    return {
        "rows": rows,
        "n_clips": len(clips),
        "n_action_clips": sum(1 for r in rows if r["action"]),
        "n_ranges": len(action),
        "ranges_hit": ranges_hit,
        "n_bad": sum(1 for r in rows if r["never"]),
    }


def ratio(k, n):
    return f"{k}/{n} ({k / n:.0%})" if n else f"{k}/{n} (n/a)"


def main():
    ap = argparse.ArgumentParser(description="Precision, recall and bad picks of a clip.py run vs an answer key.")
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

    if Path(key["video"]).stem != Path(video).stem:
        print(f"WARNING: run is for {video} but the key is for {key['video']}")
    dur = scores["input_info"]["duration"]
    if abs(dur - key["duration_s"]) > DURATION_TOL:
        print(f"WARNING: run input is {dur:.1f} s long but the key says {key['duration_s']:.1f} s")

    res = evaluate(scores, key)
    action, never = key["action"], key["never_pick"]
    print(f"Run:  {args.run_dir}  ({scores.get('tool', '?')}, {video})")
    print(f"Key:  {key_path}  (confirmed {key.get('confirmed', '?')}; "
          f"{len(action)} action ranges, {len(never)} never-pick ranges)")
    print("\nClips:")
    for r in res["rows"]:
        c = r["clip"]
        a = ", ".join(f"A{i + 1} {span(action[i])} ({ov:.1f} s)" for i, ov in r["action"]) or "-"
        n = ", ".join(f"N{i + 1} {never[i]['kind']} {span(never[i])} ({ov:.1f} s)" for i, ov in r["never"])
        verdict = ("action" if r["action"] else "no action") + (", BAD" if r["never"] else "")
        print(f"  [{c['rank']}] {hms(c['start_s'])}-{hms(c['end_s'])}  {verdict}")
        print(f"      action: {a}")
        if n:
            print(f"      never-pick: {n}")
    print("\nAction ranges:")
    for i, rng in enumerate(action):
        by = [r["clip"]["rank"] for r in res["rows"] if any(j == i for j, _ in r["action"])]
        got = "clip " + ", ".join(map(str, by)) if by else "MISSED"
        print(f"  A{i + 1} {span(rng):>15}  {got:<12} {rng['note']}")

    print(f"\nPrecision: {ratio(res['n_action_clips'], res['n_clips'])} picked clips hit an action range")
    print(f"Recall:    {ratio(len(res['ranges_hit']), res['n_ranges'])} action ranges got a clip")
    print(f"Bad picks: {res['n_bad']} clip(s) hit a never-pick range")
    total = scores.get("timing_s", {}).get("total")
    print(f"SUMMARY video={Path(video).stem} run={args.run_dir.name} tool=\"{scores.get('tool', '?')}\" "
          f"clips={res['n_clips']} precision={res['n_action_clips']}/{res['n_clips']} "
          f"recall={len(res['ranges_hit'])}/{res['n_ranges']} bad={res['n_bad']} "
          f"runtime_s={total if total is not None else 'na'}")


if __name__ == "__main__":
    main()
