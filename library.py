"""Search the footage library that index.py builds (PR 21, session 1).

  python library.py find --activity chase --min-stars 2 --min-len 6
  python library.py find --activity driving on_foot --text crash "body on" --video test1
  python library.py list                      the recordings in the library
  python library.py shotlist                  run every query of shotlist.json and print the coverage table

find prints one line per matching segment: video, start, end, activity, stars, notable line. never_use segments
(cutscene, menu or map, loading, phone) are left out unless --no-exclude-never-use is given. --json prints the
matches as JSON instead, for other tools (session 2 builds Shorts from them); they can also import find().

A query (the same keys on the command line, in find() and in shotlist.json):
  activity   one or more of index.ACTIVITIES; a segment matches if it has any of them
  min_stars / max_stars   the most wanted stars the code counted in the segment
  min_len / max_len       the segment's length in seconds
  vehicle    one or more words; matches if the segment's vehicle contains any of them ("car" matches "sports car")
  police     true: only segments where the model saw police
  text       one or more words or phrases; matches if any is in a notable line of the segment (case ignored).
             Every sheet inside the segment is read, and the row lists the lines that matched with their times
  after      one or more activities; the segment must directly follow a segment with one of them (same video)
  video      one or more recording names (without the extension)
  profile    HUD profile the recording was indexed with (gta5 now, gta6 after launch)
  exclude_never_use   default true

Standard library only. Reads only the library folder; it never opens a video or an answer key.
"""

import argparse
import json
import sys
from pathlib import Path

import index

HERE = Path(__file__).resolve().parent
SHOTLIST = HERE / "shotlist.json"
QUERY_KEYS = ("activity", "min_stars", "max_stars", "min_len", "max_len", "vehicle", "police", "text", "after",
              "video", "profile", "exclude_never_use")
SHOT_STATUS = "verify after launch"   # every topic of the shot-list carries this until GTA 6 is out


class LibraryError(Exception):
    """The library or a query can't be read: library.py stops with the reason."""


def hms(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def load_library(library=index.LIBRARY):
    """[index dict] for every recording in library.json whose index.json can be read, in name order."""
    lib = index.read_json(Path(library) / "library.json")
    if lib is None:
        raise LibraryError(f"no library at {Path(library) / 'library.json'}: run index.py on a recording first")
    out = []
    for stem in sorted(lib.get("videos", {})):
        idx = index.read_json(Path(library) / lib["videos"][stem]["index"])
        if idx is not None:
            out.append(idx)
    return out


def as_list(value):
    return [] if value is None else [value] if isinstance(value, str) else list(value)


def check_query(query):
    """The query with every key filled in; LibraryError on an unknown key, activity or a wrong type."""
    unknown = [k for k in query if k not in QUERY_KEYS]
    if unknown:
        raise LibraryError(f"unknown query key(s) {', '.join(unknown)}; the keys are {', '.join(QUERY_KEYS)}")
    q = {"activity": as_list(query.get("activity")), "after": as_list(query.get("after")),
         "vehicle": [v.lower() for v in as_list(query.get("vehicle"))],
         "text": [t.lower() for t in as_list(query.get("text"))], "video": as_list(query.get("video")),
         "profile": query.get("profile"), "police": query.get("police"),
         "exclude_never_use": query.get("exclude_never_use", True)}
    for key in ("activity", "after"):
        bad = [a for a in q[key] if a not in index.ACTIVITIES]
        if bad:
            raise LibraryError(f"unknown {key} {', '.join(bad)}; the activities are {', '.join(index.ACTIVITIES)}")
    for key, default in (("min_stars", 0), ("max_stars", None), ("min_len", 0.0), ("max_len", None)):
        value = query.get(key, default)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise LibraryError(f"{key} must be a number, not {value!r}")
        q[key] = value
    if q["police"] not in (None, True, False) or not isinstance(q["exclude_never_use"], bool):
        raise LibraryError("police and exclude_never_use must be true or false")
    return q


def segment_lines(idx, seg):
    """Every notable line of a segment, as {t, text}: one per sheet whose middle is inside it. A segment keeps only
    a few lines of its own (index.MAX_NOTABLE_LINES), so a text search reads the sheets; an index without sheets,
    or a segment shorter than a sheet, falls back to the segment's own lines."""
    every = (idx.get("settings") or {}).get("every_s")
    lines = [{"t": sh["t"], "text": sh["notable"]} for sh in idx.get("sheets", [])
             if every and sh.get("notable") and seg["start_s"] <= sh["t"] + every / 2 < seg["end_s"]]
    return lines or seg["notable"]


def find(indexes, **query):
    """The segments that match the query, as rows (dicts), in video then time order. With a text query each row
    also has text_hits: the matching lines with their times in the recording."""
    q = check_query(query)
    rows = []
    for idx in indexes:
        if q["video"] and idx["video"]["stem"] not in q["video"]:
            continue
        if q["profile"] and idx["profile"]["name"] != q["profile"]:
            continue
        segs = idx["segments"]
        for k, s in enumerate(segs):
            lines = segment_lines(idx, s)
            hits = [n for n in lines if any(t in n["text"].lower() for t in q["text"])]
            # With a text query the row shows the lines that matched; otherwise the segment's own summary lines.
            notable = " | ".join(n["text"] for n in (hits if q["text"] else s["notable"]))
            if ((q["exclude_never_use"] and s["never_use"])
                    or (q["activity"] and s["activity"] not in q["activity"])
                    or s["max_stars"] < q["min_stars"]
                    or (q["max_stars"] is not None and s["max_stars"] > q["max_stars"])
                    or s["duration_s"] < q["min_len"]
                    or (q["max_len"] is not None and s["duration_s"] > q["max_len"])
                    or (q["vehicle"] and not any(v in s["vehicle"] for v in q["vehicle"]))
                    or (q["police"] is not None and s["police_visible"] != q["police"])
                    or (q["text"] and not hits)
                    or (q["after"] and (k == 0 or segs[k - 1]["activity"] not in q["after"]))):
                continue
            # Deduplicated for display; text_hits keeps every matching line with its time, for cutting to the moment.
            notable = " | ".join(dict.fromkeys(notable.split(" | ")))
            rows.append({"video": idx["video"]["stem"], "path": idx["video"]["path"],
                         "profile": idx["profile"]["name"], "segment": k + 1,
                         "start_s": s["start_s"], "end_s": s["end_s"], "duration_s": s["duration_s"],
                         "activity": s["activity"], "never_use": s["never_use"], "max_stars": s["max_stars"],
                         "stars_s": s["stars_s"], "vehicle": s["vehicle"], "police_visible": s["police_visible"],
                         "notable": notable, "text_hits": hits if q["text"] else []})
    return rows


def print_rows(rows, out=print):
    if not rows:
        out("No segment matches.")
        return
    wide = max(len(r["video"]) for r in rows)
    for r in rows:
        # After a text search: the matching lines, each with its time in the recording.
        note = (" | ".join(f"[{hms(n['t'])}] {n['text']}" for n in r["text_hits"]) if r["text_hits"]
                else r["notable"])
        out(f"{r['video']:<{wide}}  {hms(r['start_s']):>6}-{hms(r['end_s']):<6} {r['duration_s']:>6.1f} s  "
            f"{r['activity']:<9}{' NEVER_USE' if r['never_use'] else ''}  stars {r['max_stars']}  {note[:140]}")
    out(f"{len(rows)} segment(s), {sum(r['duration_s'] for r in rows):.0f} s in all.")


# --- The shot-list ---

def load_shotlist(path=SHOTLIST):
    """The shot-list, checked: every topic has the status SHOT_STATUS and shots with a valid query."""
    data = index.read_json(path)
    if data is None:
        raise LibraryError(f"{path} is missing or not valid JSON")
    for topic in data.get("topics", []):
        if topic.get("status") != SHOT_STATUS:
            raise LibraryError(f"topic {topic.get('id')!r}: status must be {SHOT_STATUS!r}")
        if not topic.get("shots"):
            raise LibraryError(f"topic {topic.get('id')!r} has no shots")
        for shot in topic["shots"]:
            try:
                check_query(shot.get("query") or {})
            except LibraryError as e:
                raise LibraryError(f"topic {topic.get('id')!r}, shot {shot.get('id')!r}: {e}") from None
    return data


def coverage(indexes, shotlist, profile=None):
    """Per topic: its shots with the segments each query finds, and the distinct segments of the whole topic."""
    out = []
    for topic in shotlist["topics"]:
        shots, seen = [], set()
        for shot in topic["shots"]:
            query = dict(shot["query"])
            if profile:
                query["profile"] = profile
            rows = find(indexes, **query)
            seen.update((r["video"], r["segment"]) for r in rows)
            shots.append({"id": shot["id"], "want": shot["want"], "found": len(rows),
                          "seconds": round(sum(r["duration_s"] for r in rows), 1), "rows": rows})
        out.append({"id": topic["id"], "title": topic["title"], "status": topic["status"], "segments": len(seen),
                    "shots_covered": sum(1 for s in shots if s["found"]), "shots": shots})
    return out


def print_coverage(cov, out=print):
    out(f"{'topic / shot':<58} {'segments':>8} {'seconds':>8}")
    for topic in cov:
        out(f"{topic['title'][:58]:<58} {topic['segments']:>8}   ({topic['shots_covered']} of {len(topic['shots'])} "
            f"shots found; {topic['status']})")
        for shot in topic["shots"]:
            out(f"    {shot['want'][:54]:<54} {shot['found']:>8} {shot['seconds']:>8.0f}"
                + ("" if shot["found"] else "   <- nothing yet"))


def main():
    ap = argparse.ArgumentParser(description="Search the footage library built by index.py.")
    ap.add_argument("--library", type=Path, default=index.LIBRARY, help=f"library folder (default {index.LIBRARY})")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="print the segments that match")
    f.add_argument("--activity", nargs="+", choices=index.ACTIVITIES, metavar="ACTIVITY",
                   help="one or more of: " + ", ".join(index.ACTIVITIES))
    f.add_argument("--min-stars", type=int, default=0)
    f.add_argument("--max-stars", type=int)
    f.add_argument("--min-len", type=float, default=0.0, help="shortest segment, seconds")
    f.add_argument("--max-len", type=float)
    f.add_argument("--vehicle", nargs="+", help="e.g. car, motorbike, boat")
    f.add_argument("--police", action="store_true", default=None, help="only segments where the model saw police")
    f.add_argument("--text", nargs="+", help="words or phrases to look for in the notable lines")
    f.add_argument("--after", nargs="+", choices=index.ACTIVITIES, metavar="ACTIVITY",
                   help="the segment must directly follow one with this activity")
    f.add_argument("--video", nargs="+", help="recording name(s) without the extension")
    f.add_argument("--profile", help="only recordings indexed with this HUD profile")
    f.add_argument("--exclude-never-use", action=argparse.BooleanOptionalAction, default=True,
                   help="leave out cutscene, menu, loading and phone segments (default on)")
    f.add_argument("--json", action="store_true", help="print the matches as JSON")
    sub.add_parser("list", help="the recordings in the library")
    s = sub.add_parser("shotlist", help="run every query of the shot-list and print the coverage table")
    s.add_argument("file", type=Path, nargs="?", default=SHOTLIST)
    s.add_argument("--profile", help="only recordings indexed with this HUD profile")
    s.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        indexes = load_library(args.library)
        if args.cmd == "find":
            rows = find(indexes, **{k: getattr(args, k) for k in QUERY_KEYS})
            if args.json:
                print(json.dumps(rows, indent=1, ensure_ascii=False))
            else:
                print_rows(rows)
        elif args.cmd == "list":
            for idx in indexes:
                segs = idx["segments"]
                never = sum(x["duration_s"] for x in segs if x["never_use"])
                print(f"{idx['video']['stem']}  {hms(idx['video']['duration_s'])}  profile {idx['profile']['name']}  "
                      f"{len(segs)} segments, {hms(idx['video']['duration_s'] - never)} usable, {hms(never)} never_use  "
                      f"indexed {idx['indexed']}")
        else:
            cov = coverage(indexes, load_shotlist(args.file), args.profile)
            if args.json:
                print(json.dumps(cov, indent=1, ensure_ascii=False))
            else:
                print(f"Shot-list {args.file.name} against {len(indexes)} recording(s) in {args.library}"
                      + (f", profile {args.profile}" if args.profile else ""))
                print_coverage(cov)
    except LibraryError as e:
        sys.exit(f"ERROR: {e}")


if __name__ == "__main__":
    main()
