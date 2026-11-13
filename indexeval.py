"""Measure a footage index (index.py) against what is known about the recording. MEASURING ONLY: index.py and
library.py never read the answer keys; this script is the only place they meet an index.

Two checks:
1. The owner's confirmed moments (MOMENTS below, test1 only): for each, the segments the index has there, and
   whether the activity and the wanted stars are right.
2. The recording's answer key (eval/answer_keys/<video>.json), if it has one:
   - every never_pick range: how much of it the index marks never_use (should be nearly all of it)
   - every action range: what the index calls it, its stars, and how much of it is wrongly never_use (should be 0)
   - never_use time outside every never_pick range, segment by segment (each is a wrong never_use, or something
     the key calls neutral)

Usage:  python indexeval.py <video name, e.g. test1> [--library DIR]
"""

import argparse
import json
import sys
from pathlib import Path

import index

HERE = Path(__file__).resolve().parent
KEY_DIR = HERE / "eval" / "answer_keys"
VEHICLE = ("driving", "chase")
# The owner's confirmed moments (PR 21 brief). start, end in seconds; a point moment gets 3 s each side, half a sheet.
# want: the activities that count as right; text: words one of which must be in a notable line (None = not checked);
# stars: True if wanted stars must have been counted there.
MOMENTS = {"test1": [
    {"what": "0:50-1:10 gun out + carjack", "start": 50, "end": 70, "want": ("shootout",),
     "text": ("gun", "car"), "stars": False},
    {"what": "1:20-2:00 driving with wanted stars", "start": 80, "end": 120, "want": VEHICLE, "text": None,
     "stars": True},
    {"what": "2:10 shootout", "start": 127, "end": 133, "want": ("shootout",), "text": None, "stars": False},
    {"what": "2:50 body on road", "start": 167, "end": 173, "want": ("on_foot", "shootout", "driving", "chase"),
     "text": ("body", "ground"), "stars": False},
    {"what": "3:10-4:10 driving with wanted stars", "start": 190, "end": 250, "want": VEHICLE, "text": None,
     "stars": True}]}


def hms(seconds):
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:04.1f}"


def overlap(seg, start, end):
    return max(0.0, min(seg["end_s"], end) - max(seg["start_s"], start))


def in_range(segments, start, end):
    """[(segment, seconds of it inside start..end)] for the segments that share time with the range."""
    return [(s, overlap(s, start, end)) for s in segments if overlap(s, start, end) > 0]


def shares(parts, length):
    """'label 80%, label 20%' by seconds."""
    by = {}
    for s, sec in parts:
        by[s["activity"]] = by.get(s["activity"], 0.0) + sec
    return ", ".join(f"{a} {sec / length:.0%}" for a, sec in sorted(by.items(), key=lambda kv: -kv[1]))


def check_moments(segments, moments, out=print):
    hits = 0
    for m in moments:
        parts = in_range(segments, m["start"], m["end"])
        length = m["end"] - m["start"]
        right_s = sum(sec for s, sec in parts if s["activity"] in m["want"] and not s["never_use"])
        # A range is right when the wanted activity fills most of it; a short "gun out" only has to appear.
        act_ok = right_s >= (0.5 * length if m["want"] == VEHICLE else 1.0)
        stars = max((s["max_stars"] for s, _ in parts), default=0)
        stars_ok = stars >= 1 if m["stars"] else True
        lines = " | ".join(n["text"] for s, _ in parts for n in s["notable"]
                           if m["start"] - 6 <= n["t"] < m["end"])
        text_ok = True if m["text"] is None else any(t in lines.lower() for t in m["text"])
        ok = act_ok and stars_ok and text_ok
        hits += ok
        out(f"{'HIT ' if ok else 'MISS'} {m['what']}")
        out(f"       label: {shares(parts, length)}; stars counted: {stars}"
            f"{'' if m['stars'] else ' (not required)'}; activity {'right' if act_ok else 'WRONG'}"
            + ("" if m["text"] is None else f"; notable mentions {'/'.join(m['text'])}: {'yes' if text_ok else 'NO'}"))
        out(f"       notable: {lines[:230]}")
    out(f"MOMENTS: {hits} of {len(moments)} hit")
    return hits


def check_key(segments, key, out=print):
    never_total = never_caught = 0.0
    out("never_pick ranges (how much the index marks never_use):")
    for r in key["never_pick"]:
        length = r["end_s"] - r["start_s"]
        parts = in_range(segments, r["start_s"], r["end_s"])
        caught = sum(sec for s, sec in parts if s["never_use"])
        never_total += length
        never_caught += caught
        out(f"  {'OK  ' if caught >= 0.9 * length else 'LOW ' if caught else 'MISS'} {hms(r['start_s'])}-{hms(r['end_s'])} "
            f"{r['kind']:<10} {caught / length:>4.0%} never_use  ({shares(parts, length)})")
    action_total = action_wrong = 0.0
    out("action ranges (what the index calls them):")
    for r in key["action"]:
        length = r["end_s"] - r["start_s"]
        parts = in_range(segments, r["start_s"], r["end_s"])
        wrong = sum(sec for s, sec in parts if s["never_use"])
        action_total += length
        action_wrong += wrong
        stars = max((s["max_stars"] for s, _ in parts), default=0)
        out(f"  {hms(r['start_s'])}-{hms(r['end_s'])}  {shares(parts, length)}; max stars {stars}; "
            f"never_use {wrong:.1f} s  [{r['note'][:60]}]")
    out("never_use segments outside every never_pick range:")
    extra = 0.0
    for s in segments:
        if not s["never_use"]:
            continue
        inside = sum(max(0.0, min(s["end_s"], r["end_s"]) - max(s["start_s"], r["start_s"]))
                     for r in key["never_pick"])
        outside = s["duration_s"] - inside
        if outside > 0.5:   # half a second of slack: the key's edges are on a 0.5 s grid
            extra += outside
            note = "; ".join(n["text"] for n in s["notable"][:2])
            out(f"  {hms(s['start_s'])}-{hms(s['end_s'])} {s['activity']:<9} {outside:5.1f} s outside  "
                f"({s['decided_by']})  {note[:90]}")
    usable_total = key["duration_s"] - never_total
    out(f"KEY SUMMARY: never_pick time marked never_use {never_caught:.0f} of {never_total:.0f} s "
        f"({never_caught / never_total:.0%}); action time wrongly never_use {action_wrong:.1f} of "
        f"{action_total:.0f} s; never_use outside never_pick ranges {extra:.0f} s of {usable_total:.0f} s")


def main():
    ap = argparse.ArgumentParser(description="Measure a footage index against the owner's moments and the answer key.")
    ap.add_argument("video", help="recording name without the extension, e.g. test1")
    ap.add_argument("--library", type=Path, default=index.LIBRARY)
    args = ap.parse_args()
    idx = index.read_json(args.library / args.video / "index.json")
    if idx is None:
        sys.exit(f"ERROR: no index for {args.video} in {args.library}")
    segs = idx["segments"]
    print(f"{args.video}: {len(segs)} segments, model {idx['model']['name']}, prompt {idx['model']['prompt_version']}, "
          f"one sheet per {idx['settings']['every_s']:g} s")
    if args.video in MOMENTS:
        check_moments(segs, MOMENTS[args.video])
    key_path = KEY_DIR / f"{args.video}.json"
    if key_path.is_file():
        check_key(segs, json.loads(key_path.read_text(encoding="utf-8")))
    else:
        print(f"(no answer key for {args.video})")


if __name__ == "__main__":
    main()
