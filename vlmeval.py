"""The model as a judge (v6): compare each candidate's veto with the owner's rating, where one exists.

A --vlm run's scores.json lists every candidate in its vlm block, vetoed ones included. Each candidate's rating
is found with eval.py's own rule (a rating belongs to the rated clip's peak; see eval.py), then:

- vetoed but postable: the costly mistake, a clip the owner would post is thrown out
- kept but not postable: the veto let through a clip the owner rejected
- agreements: vetoed and not postable, or kept and postable
- unrated: listed, not counted

A model error counts as kept (errors never veto). A filler counts as vetoed: the model vetoed it, even though
it was used to make up the count.

Usage:  python vlmeval.py <run_dir> [<run_dir> ...]
  Several runs are added up per model at the end. The last line of each run starts with JUDGE (for scripts).
"""

import argparse
import json
import sys
from pathlib import Path

import eval as ev

OUTCOMES = ("vetoed_postable", "kept_not_postable", "agree_vetoed", "agree_kept", "unrated")


def outcome(vetoed, rating):
    """One of OUTCOMES for a candidate's veto and its rating (None = unrated)."""
    if rating is None:
        return "unrated"
    if vetoed:
        return "vetoed_postable" if rating["postable"] else "agree_vetoed"
    return "agree_kept" if rating["postable"] else "kept_not_postable"


def judge_rows(scores, rated):
    """(candidate, rating or None, outcome) for every candidate of a --vlm run, in rank order."""
    rows = []
    for c in scores["vlm"]["candidates"]:
        g, _ = ev.rating_for(c, rated)
        vetoed = c["vlm"]["error"] is None and c["vlm"]["vetoed"]
        rows.append((c, g, outcome(vetoed, g)))
    return rows


def tally(rows):
    return {k: sum(1 for _, _, o in rows if o == k) for k in OUTCOMES}


def main():
    ap = argparse.ArgumentParser(description="Compare a --vlm run's vetoes with the owner's ratings.")
    ap.add_argument("run_dirs", type=Path, nargs="+")
    args = ap.parse_args()
    totals = {}
    for run_dir in args.run_dirs:
        scores = json.loads((run_dir / "scores.json").read_text(encoding="utf-8"))
        if "vlm" not in scores:
            sys.exit(f"ERROR: {run_dir} is not a --vlm run (no vlm block in scores.json)")
        video = Path(scores["input"]).stem
        v = scores["vlm"]
        rows = judge_rows(scores, ev.load_ratings(video))
        print(f"Run: {run_dir}  ({video}, {v['model']}, prompt {v['prompt_version']}, sheet v{v['sheet_version']})")
        print(f"  {'#':>3}  {'candidate':<17} {'category':<26} {'veto':<14} {'owner':<8} outcome")
        for c, g, o in rows:
            m = c["vlm"]
            veto = "error, kept" if m["error"] else ("VETO, filler" if m["filler"] else "VETO" if m["vetoed"] else "-")
            owner = "-" if g is None else ("yes" if g["postable"] else "no")
            print(f"  [{c['candidate_rank']}]  {ev.hms(c['start_s']) + '-' + ev.hms(c['end_s']):<17} "
                  f"{m['category'] or '-':<26} {veto:<14} {owner:<8} {o}")
        t = tally(rows)
        for k in OUTCOMES:
            totals.setdefault(v["model"], dict.fromkeys(OUTCOMES, 0))[k] += t[k]
        print(f"  vetoed but postable (costly): {t['vetoed_postable']}; kept but not postable: {t['kept_not_postable']}; "
              f"agreements: {t['agree_vetoed'] + t['agree_kept']} ({t['agree_vetoed']} vetoed, {t['agree_kept']} kept); "
              f"unrated: {t['unrated']}")
        print(f"JUDGE video={video} run={run_dir.name} model={v['model']} prompt={v['prompt_version']} "
              + " ".join(f"{k}={t[k]}" for k in OUTCOMES) + "\n")
    if len(args.run_dirs) > 1:
        print("Totals per model:")
        for model, t in totals.items():
            rated = sum(t[k] for k in OUTCOMES if k != "unrated")
            print(f"  {model}: {rated} rated candidates; vetoed but postable {t['vetoed_postable']}, kept but not "
                  f"postable {t['kept_not_postable']}, agreements {t['agree_vetoed'] + t['agree_kept']} "
                  f"({t['agree_vetoed']} vetoed, {t['agree_kept']} kept); unrated {t['unrated']}")


if __name__ == "__main__":
    main()
