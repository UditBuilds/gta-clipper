"""Search the footage library that index.py builds (PR 21, session 1).

  python library.py find --activity chase --min-stars 2 --min-len 6
  python library.py find --activity driving on_foot --text crash "body on" --video test1
  python library.py find --stars-rise --min-stars 2      moments where the wanted stars go up, as cut windows
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
             Every sheet inside the segment is read, and the row lists the lines that matched with their times.
             A word the line only denies does not match: "no crashes or police" matches neither "crash" nor
             "police" (see positive_text). The other filters are applied first; text only reads what they leave
  stars_rise true: instead of segments, the moments where the wanted-star count goes up (0 to 1, 2 to 3, ...),
             each as a cut window from before_s (2) seconds before the change to after_s (2) seconds after it.
             min_stars / max_stars then mean the count after the rise; activity, vehicle, police, text, after and
             min_len / max_len are those of the segment the change is in. See star_rises()
  before_s / after_s   the star-rise window's lead-in and its length after the change, in seconds (2 and 2)
  after      one or more activities; the segment must directly follow a segment with one of them (same video)
  video      one or more recording names (without the extension)
  profile    HUD profile the recording was indexed with (gta5 now, gta6 after launch)
  exclude_never_use   default true

Standard library only. Reads only the library folder; it never opens a video or an answer key.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import clip
import index

HERE = Path(__file__).resolve().parent
SHOTLIST = HERE / "shotlist.json"
QUERY_KEYS = ("activity", "min_stars", "max_stars", "min_len", "max_len", "vehicle", "police", "text", "after",
              "video", "profile", "exclude_never_use", "stars_rise", "before_s", "after_s")
RISE_BEFORE_S, RISE_AFTER_S = 2.0, 2.0   # a star-rise window: this long before the change, at least this long after
RISE_STEADY_S = 1.0                      # the count must hold this long before the change and this long after it
# Words that deny what follows them, to the end of the sentence or to a word that turns it round. A comma does not
# end it: "no police, crashes or fire" denies all three, and a missed match costs less than a wrong shot.
NEGATIONS = ("no", "not", "without", "never", "none", "nobody", "nothing", "neither", "nor", "zero", "lacking",
             "lacks", "absent")
NEGATION_ENDS = ("but", "however", "while", "whereas", "then", "yet", "though", "although", "except", "until",
                 "before", "after", "when", "as")
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
            idx["_dir"] = str((Path(library) / lib["videos"][stem]["index"]).parent)   # where its hud.json is
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
         "exclude_never_use": query.get("exclude_never_use", True), "stars_rise": query.get("stars_rise") or False}
    for key in ("activity", "after"):
        bad = [a for a in q[key] if a not in index.ACTIVITIES]
        if bad:
            raise LibraryError(f"unknown {key} {', '.join(bad)}; the activities are {', '.join(index.ACTIVITIES)}")
    for key, default in (("min_stars", 0), ("max_stars", None), ("min_len", 0.0), ("max_len", None),
                         ("before_s", RISE_BEFORE_S), ("after_s", RISE_AFTER_S)):
        value = query.get(key, default)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise LibraryError(f"{key} must be a number, not {value!r}")
        q[key] = value
    if (q["police"] not in (None, True, False) or not isinstance(q["exclude_never_use"], bool)
            or not isinstance(q["stars_rise"], bool)):
        raise LibraryError("police, exclude_never_use and stars_rise must be true or false")
    for key in ("before_s", "after_s"):
        if q[key] is None:
            q[key] = RISE_BEFORE_S if key == "before_s" else RISE_AFTER_S
        if q[key] < 0:
            raise LibraryError(f"{key} can't be negative")
    return q


def segment_lines(idx, seg):
    """Every notable line of a segment, as {t, text}: one per sheet whose middle is inside it. A segment keeps only
    a few lines of its own (index.MAX_NOTABLE_LINES), so a text search reads the sheets; an index without sheets,
    or a segment shorter than a sheet, falls back to the segment's own lines."""
    every = (idx.get("settings") or {}).get("every_s")
    lines = [{"t": sh["t"], "text": sh["notable"]} for sh in idx.get("sheets", [])
             if every and sh.get("notable") and seg["start_s"] <= sh["t"] + every / 2 < seg["end_s"]]
    return lines or seg["notable"]


def positive_text(text):
    """A notable line, lower case, with everything it denies blanked out, so a search can't match it.

    A denying word (NEGATIONS, or a word ending in n't) covers the words after it to the end of the sentence, or to a
    word that turns the sentence round (NEGATION_ENDS: "no police but a crash" keeps "a crash"). "or", "and" and
    commas carry it on: in "no crashes or police" both are denied. The line keeps its length: blanked letters
    become spaces, so nothing new is joined together."""
    low = text.lower()
    out, denied = [], False
    for piece in re.findall(r"[a-z0-9']+|[^a-z0-9']+", low):
        if not piece[0].isalnum() and piece[0] != "'":
            if any(c in piece for c in ".;!?"):
                denied = False
            out.append(piece)
            continue
        word = piece.strip("'")
        if word in NEGATION_ENDS:
            denied = False
        if word in NEGATIONS or word.endswith("n't"):
            denied = True
        out.append(" " * len(piece) if denied else piece)
    return "".join(out)


def text_hits(lines, terms):
    """(the lines a term is in, the lines that only deny one): each a list of {t, text}."""
    hits, denied = [], []
    for n in lines:
        low, kept = n["text"].lower(), positive_text(n["text"])
        if any(t in kept for t in terms):
            hits.append(n)
        elif any(t in low for t in terms):
            denied.append(n)
    return hits, denied


def passes(q, s, before):
    """True when a segment passes every filter of the query but text (before: the segment before it, or None)."""
    return not ((q["exclude_never_use"] and s["never_use"])
                or (q["activity"] and s["activity"] not in q["activity"])
                or (q["vehicle"] and not any(v in s["vehicle"] for v in q["vehicle"]))
                or (q["police"] is not None and s["police_visible"] != q["police"])
                or (q["after"] and (before is None or before["activity"] not in q["after"])))


def find(indexes, **query):
    """The segments that match the query, as rows (dicts), in video then time order. The structured filters
    (activity, stars, length, vehicle, police, after, never_use) are applied first; the text search then reads only
    the segments they leave. With a text query each row also has text_hits: the matching lines with their times in
    the recording, and text_denied: how many lines held a search word only after "no", "not" or "without".

    With stars_rise the rows are star-rise windows instead (star_rises): one per moment the wanted-star count goes
    up."""
    q = check_query(query)
    if q["stars_rise"]:
        return star_rises(indexes, q)
    rows = []
    for idx in indexes:
        if q["video"] and idx["video"]["stem"] not in q["video"]:
            continue
        if q["profile"] and idx["profile"]["name"] != q["profile"]:
            continue
        segs = idx["segments"]
        for k, s in enumerate(segs):
            if (not passes(q, s, segs[k - 1] if k else None)
                    or s["max_stars"] < q["min_stars"]
                    or (q["max_stars"] is not None and s["max_stars"] > q["max_stars"])
                    or s["duration_s"] < q["min_len"]
                    or (q["max_len"] is not None and s["duration_s"] > q["max_len"])):
                continue
            hits, denied = text_hits(segment_lines(idx, s), q["text"]) if q["text"] else ([], [])
            if q["text"] and not hits:
                continue
            # With a text query the row shows the lines that matched; otherwise the segment's own summary lines.
            # Deduplicated for display; text_hits keeps every matching line with its time, for cutting to the moment.
            notable = " | ".join(dict.fromkeys(n["text"] for n in (hits if q["text"] else s["notable"])))
            rows.append({"video": idx["video"]["stem"], "path": idx["video"]["path"],
                         "profile": idx["profile"]["name"], "segment": k + 1,
                         "start_s": s["start_s"], "end_s": s["end_s"], "duration_s": s["duration_s"],
                         "activity": s["activity"], "never_use": s["never_use"], "max_stars": s["max_stars"],
                         "stars_s": s["stars_s"], "vehicle": s["vehicle"], "police_visible": s["police_visible"],
                         "notable": notable, "text_hits": hits, "text_denied": len(denied),
                         "confidence": s.get("confidence")})
    return rows


# --- Wanted-star changes ---

def timeline_of(idx):
    """A recording's HUD per half second (index.hud_timeline), from the HUD samples index.py saved next to its
    index: the stars showing and how many, the phone and no-minimap stretches. LibraryError when they aren't there
    (an index made by hand, or a library folder that was moved without its hud.json)."""
    if "_timeline" in idx:
        return idx["_timeline"]
    folder = idx.get("_dir")
    saved = index.read_json(Path(folder) / "hud.json") if folder else None
    if not saved or not saved.get("samples"):
        raise LibraryError(f"{idx['video']['stem']}: no saved HUD samples (hud.json) next to its index, so its star "
                           "changes can't be read: run index.py on the recording again")
    try:
        prof = clip.load_profile(idx["profile"]["name"])
    except clip.ProfileError as e:
        raise LibraryError(f"{idx['video']['stem']}: {e}") from None
    samples = [tuple(x) for x in saved["samples"]]
    tl = index.hud_timeline(samples, idx["video"]["duration_s"], prof)
    tl["samples"] = samples
    idx["_timeline"] = tl
    return tl


def star_changes(count, steady_s=RISE_STEADY_S):
    """Every time the star count settles on a new number: [(step, count before, count after)]. The count must have
    held for steady_s before the change and must hold for steady_s after it, so a one-sample misread, or the half
    second in which a star is being drawn, is not a change. count: one number per clip.BUCKET of the recording."""
    need = max(1, round(steady_s / clip.BUCKET))
    out, i = [], need
    while i <= len(count) - need:
        before, after = count[i - 1], count[i]
        if (after != before and all(c == before for c in count[i - need:i])
                and all(c == after for c in count[i:i + need])):
            out.append((i, before, after))
            i += need
        else:
            i += 1
    return out


def sample_at(samples, t):
    """The saved HUD sample nearest to t: (time, star score, minimap seen, phone seen, star count)."""
    return min(samples, key=lambda x: abs(x[0] - t))


def rise_windows(idx, tl, before_s=RISE_BEFORE_S, after_s=RISE_AFTER_S):
    """The star rises of one recording as cut windows. Each window starts before_s before the change and ends
    after_s after it, and is kept only when
      - the whole window is usable footage: no never_use segment (cutscene, menu, phone, loading) and no
        no-minimap or phone stretch touches it, and it lies inside the recording;
      - the HUD read at both ends confirms it: at the window's first half second the count is the lower number, at
        its last the higher one (or more), and the minimap is on screen at both.
    Rows are in time order."""
    segs, step = idx["segments"], clip.BUCKET
    rows = []
    for i, low, high in star_changes(tl["count"]):
        if high <= low:
            continue
        change = i * step
        start, end = round(change - before_s, 2), round(change + after_s, 2)
        if start < 0 or end > idx["video"]["duration_s"]:
            continue
        a, b = int(start / step), min(tl["n"], int(round(end / step)))
        spanned = [k for k, s in enumerate(segs) if s["start_s"] < end and start < s["end_s"]]
        if (any(segs[k]["never_use"] for k in spanned) or any(tl["no_minimap"][a:b]) or any(tl["phone"][a:b])
                or not spanned):
            continue
        first, last = tl["count"][a], tl["count"][b - 1]
        ends = [sample_at(tl["samples"], start), sample_at(tl["samples"], end - step)] if tl.get("samples") else []
        confirmed = (first == low and last >= high and min(tl["count"][i:b] or [high]) >= high
                     and all(x[2] for x in ends))
        if not confirmed:
            continue
        k = next(k for k in spanned if segs[k]["start_s"] <= change < segs[k]["end_s"] or k == spanned[-1])
        rows.append({"change_s": change, "start_s": start, "end_s": end, "duration_s": round(end - start, 2),
                     "stars_before": low, "stars_after": high, "segment": k + 1,
                     "segments": [n + 1 for n in spanned],
                     "hud_start": {"t": start, "stars": first, "minimap": True},
                     "hud_end": {"t": round(end - step, 2), "stars": last, "minimap": True},
                     # how long the higher count then holds: room to make the window longer after the change
                     "holds_s": round(next((j for j in range(i, tl["n"]) if tl["count"][j] < high), tl["n"]) * step
                                      - change, 1)})
    return rows


def star_rises(indexes, q):
    """find() with stars_rise: every star rise of the recordings that pass the query, as rows with the window
    (start_s, end_s), the change (change_s, stars_before, stars_after) and the HUD read at both ends."""
    rows = []
    for idx in indexes:
        if q["video"] and idx["video"]["stem"] not in q["video"]:
            continue
        if q["profile"] and idx["profile"]["name"] != q["profile"]:
            continue
        segs = idx["segments"]
        for r in rise_windows(idx, timeline_of(idx), q["before_s"], q["after_s"]):
            k = r["segment"] - 1
            s = segs[k]
            if (not passes(q, s, segs[k - 1] if k else None) or r["stars_after"] < q["min_stars"]
                    or (q["max_stars"] is not None and r["stars_after"] > q["max_stars"])
                    or s["duration_s"] < q["min_len"]
                    or (q["max_len"] is not None and s["duration_s"] > q["max_len"])):
                continue
            hits, denied = text_hits(segment_lines(idx, s), q["text"]) if q["text"] else ([], [])
            if q["text"] and not hits:
                continue
            rows.append({"video": idx["video"]["stem"], "path": idx["video"]["path"],
                         "profile": idx["profile"]["name"], **r, "activity": s["activity"], "never_use": False,
                         "max_stars": r["stars_after"], "vehicle": s["vehicle"],
                         "police_visible": s["police_visible"],
                         "notable": " | ".join(dict.fromkeys(n["text"] for n in (hits or s["notable"]))),
                         "text_hits": hits, "text_denied": len(denied), "confidence": s.get("confidence")})
    return rows


def print_rows(rows, out=print):
    if not rows:
        out("No segment matches.")
        return
    wide = max(len(r["video"]) for r in rows)
    if "change_s" in rows[0]:     # star rises
        for r in rows:
            out(f"{r['video']:<{wide}}  stars {r['stars_before']} -> {r['stars_after']} at {hms(r['change_s'])}  "
                f"cut {r['start_s']:.1f}-{r['end_s']:.1f} s ({r['duration_s']:.1f} s)  {r['activity']:<9}  "
                f"HUD read: {r['hud_start']['stars']} star(s) at the start, {r['hud_end']['stars']} at the end; "
                f"the higher count holds {r['holds_s']:.0f} s")
        out(f"{len(rows)} star rise(s).")
        return
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
    f.add_argument("--stars-rise", action="store_true",
                   help="the moments where the wanted-star count goes up, as cut windows, instead of segments")
    f.add_argument("--before-s", type=float, default=RISE_BEFORE_S,
                   help=f"with --stars-rise: seconds of the window before the change (default {RISE_BEFORE_S:g})")
    f.add_argument("--after-s", type=float, default=RISE_AFTER_S,
                   help=f"with --stars-rise: seconds of the window after the change (default {RISE_AFTER_S:g})")
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
