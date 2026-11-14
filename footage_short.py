"""Mode B (PR 22, session 2): turn one script into a finished vertical Short built from the owner's OWN gameplay
footage, picked from the footage library (index.py, library.py). Rockstar's pictures are only the fallback.

  .venv-tts\\Scripts\\python.exe footage_short.py script.json            check, pick footage, render, write the files
  .venv-tts\\Scripts\\python.exe footage_short.py script.json --check    only check and show the picks; render nothing
  .venv-tts\\Scripts\\python.exe footage_short.py script.json --allow-stale    (the owner's choice, as in make_short.py)

The script is a make_short.py script (title, description, hashtags, lines with "say", "facts" and "beats"). New here:

  a beat may ask for footage with "shot" instead of naming a file or asking for a picture:
      {"beat": "full", "shot": {"activity": ["chase"], "min_stars": 2}}          a library.py query, as in shotlist.json
      {"beat": "card", "shot": "police-chase/long-chase", "card_aspect": "16:9"}  a shot of shotlist.json, by its ids
      {"beat": "full", "shot": {"stars_rise": true}, "query": "police car"}       "query" is the Rockstar fallback
    "full" fills the screen with the middle of the gameplay; "card" floats the gameplay as a framed card over a
    blurred copy of itself (the explainer's look), so the HUD stays in view; "banner" puts a pill over it.
  "footage": {"game_audio": true, "game_volume": 1.0, "profile": "gta5"}   all optional. game_audio keeps the game's
    own sound in, ducked under the voice (default on); profile limits the picks to recordings indexed with that HUD
    profile (so a GTA 6 Short never shows GTA V footage, and the other way round).

What it does, in order, stopping at the first step that finds a problem:
  1. checks the title, description, hashtags, word count and every line's facts (make_short.py's own checks and
     facts.py's 3-day stale-fact guard; --allow-stale is the owner's);
  2. makes the voice once to learn how long every beat will be on screen (explainer.timing);
  3. for each "shot" beat runs its query and picks footage (pick_beats): the best unused piece wins, where best is
     more action (a chase or a shootout before a calm drive, more wanted stars, police in view), a star rise for a
     beat that asks for wanted stars, and a recording this Short hasn't used yet. The piece is then cut to the
     beat's exact length on clean edges (place): never into a never_use part (cutscene, menu or map, phone, loading),
     never within a second of a star change at either end, and with enough clean footage after it for a text card
     blurred over it and, for the first beat, before it for the loop ending;
  4. a beat that finds no footage falls back to a Rockstar picture (picker.py) and says so in picks.json;
  5. renders with explainer.py (captions, am_puck, music bed, sound effects as in every explainer Short), then reads
     the render's own log and checks every stretch of footage that got on screen against the index once more;
  6. writes to C:\\AI\\gta\\shorts\\<date>-<slug>\\: short.mp4, script.json, script_resolved.json, upload.txt, claims.txt,
     pacing.txt, picks.json (beat -> video, start, end, why) and picks.html (a thumbnail per beat).
The Short is READY when it is 20-39 s long, every beat got on screen and every stretch of footage is clean.
Nothing is ever uploaded.

Needs .venv-tts (it renders with explainer.py). Everything but the render and the voice timing is standard library.
"""

import argparse
import html
import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote

import clip
import facts as facts_file
import index
import library
import make_short
import picker
import tag_media

SHORTS_DIR = make_short.SHORTS_DIR
USED_FOOTAGE = "_used_footage.json"       # in the shorts folder: the footage each finished Short showed
RECENT_SHORTS = 5                         # footage shown in this many Shorts back is avoided when there is other
LENGTH_S = (20.0, 39.0)                   # a footage Short's length; outside it the Short is NOT READY
WORDS_TARGET = (65, 105)                  # about what fills 20-35 s; outside it warns
WORDS_LIMIT = (50, 125)                   # outside this it can't land in LENGTH_S, so nothing is rendered
FOOTAGE_KEYS = ("game_audio", "game_volume", "profile")
SHOT_BEATS = ("full", "card", "banner")   # the beat types footage can fill
GRID_S = 0.5                              # footage starts on half seconds: the HUD is read twice a second
EDGE_S = 1.0                              # a cut is at least this far from a wanted-star change
GUARD_S = 0.5                             # ...and footage keeps this far from a never_use part
APART_S = 1.0                             # two beats never show footage closer together than this
RISE_LEAD_S = 2.0                         # a star-rise beat starts this long before the change (less in a short beat)
RISE_LEAD_SHARE = 0.45                    # ...but the change is never later than this share of the beat
# How much is going on in a segment, by its activity. A chase or a shootout beats a calm drive for the same query.
ACTION = {"chase": 3.0, "shootout": 3.0, "flying": 2.0, "boat": 2.0, "swimming": 1.5, "driving": 1.5, "on_foot": 1.0,
          "other": 0.5}
# What a beat falls back to when its query finds no footage and it names no Rockstar "query" itself.
FALLBACK_WORDS = {"chase": "police car", "shootout": "gun", "flying": "helicopter", "boat": "boat",
                  "swimming": "beach water", "driving": "car street", "on_foot": "street people", "other": "city skyline"}


class FootageError(Exception):
    """The script can't be made into a footage Short as it is. The text lists every problem."""


# --- What footage a recording has ---

def usable_spans(idx):
    """The stretches of a recording a Short may show, as [start, end] in seconds: its segments that are not
    never_use (cutscene, menu or map, phone, loading), joined where they follow each other."""
    spans = []
    for s in idx["segments"]:
        if s["never_use"]:
            continue
        if spans and abs(spans[-1][1] - s["start_s"]) < 1e-6:
            spans[-1][1] = s["end_s"]
        else:
            spans.append([s["start_s"], s["end_s"]])
    return spans


def change_times(idx):
    """The seconds at which the wanted-star count changes (up or down), or [] when the recording has no saved HUD
    samples to read them from."""
    try:
        tl = library.timeline_of(idx)
    except library.LibraryError:
        return []
    return [i * clip.BUCKET for i, _, _ in library.star_changes(tl["count"])]


def clean(idx, start, end):
    """True when start..end of a recording is footage a Short may show: inside one usable stretch, GUARD_S away from
    its ends (a never_use part or the recording's own end is on the other side)."""
    return any(a + GUARD_S <= start + 1e-6 and end <= b - GUARD_S + 1e-6 for a, b in usable_spans(idx))


def overlaps(video, start, end, taken):
    return any(v == video and start < b + APART_S and a < end + APART_S for v, a, b in taken)


# --- Cutting a piece of footage to a beat ---

def place(idx, cand, seconds, lead=0.0, tail=None, taken=()):
    """Where a beat of `seconds` should start in a candidate's footage, or None when it doesn't fit.

    cand is a row of library.find(): a segment, or a star rise (it has change_s). lead is the clean footage needed
    before the start (the loop ending plays that much of what comes before the first beat); tail is the clean footage
    needed from the start on (the beat itself, or longer when a text card is blurred over it afterwards).
      - a segment: the beat stays inside the segment, its two ends at least EDGE_S from any star change, as close as
        it gets to the moment the query's words were seen (else the segment's middle);
      - a star rise: the beat starts RISE_LEAD_S before the change (never later than RISE_LEAD_SHARE into the beat),
        and the higher count holds to the beat's end.
    Either way everything shown (lead, beat, tail) is clean footage, and not footage another beat already shows."""
    tail = max(seconds, tail or 0.0)
    video = idx["video"]["stem"]
    changes = change_times(idx)

    def ok(start):
        return (start - lead >= 0 and clean(idx, start - lead, start + tail)
                and not overlaps(video, start - lead, start + tail, taken))

    if "change_s" in cand:
        before = min(RISE_LEAD_S, RISE_LEAD_SHARE * seconds)
        start = round((cand["change_s"] - before) / GRID_S) * GRID_S
        end = start + seconds
        others = [c for c in changes if abs(c - cand["change_s"]) > 1e-6]
        if (ok(start) and end <= cand["change_s"] + cand["holds_s"] + 1e-6 and cand["change_s"] + EDGE_S <= end + 1e-6
                and all(abs(c - start) >= EDGE_S and abs(c - end) >= EDGE_S for c in others)):
            return start
        return None
    hits = cand.get("text_hits") or []
    every = (idx.get("settings") or {}).get("every_s") or 6.0
    anchor = hits[0]["t"] + every / 2 if hits else (cand["start_s"] + cand["end_s"]) / 2
    best = None
    start = -(-cand["start_s"] // GRID_S) * GRID_S          # the first half second inside the segment
    while start + seconds <= cand["end_s"] + 1e-6:
        end = start + seconds
        if ok(start) and all(abs(c - start) >= EDGE_S and abs(c - end) >= EDGE_S for c in changes):
            inside = sum(1 for c in changes if start < c < end)      # a beat with no star change in it is calmer
            key = (inside, abs(start + seconds / 2 - anchor))
            if best is None or key < best[0]:
                best = (key, start)
        start += GRID_S
    return best[1] if best else None


# --- Choosing footage for every beat ---

def shot_query(shot, shotlist=None):
    """A beat's "shot" as a library query: a query object as it is, or "topic-id/shot-id" looked up in the
    shot-list. FootageError says what is wrong with it."""
    if isinstance(shot, str):
        topic_id, _, shot_id = shot.partition("/")
        shotlist = shotlist if shotlist is not None else library.load_shotlist()
        for topic in shotlist.get("topics", []):
            for item in topic["shots"] if topic.get("id") == topic_id else []:
                if item["id"] == shot_id:
                    return dict(item["query"])
        raise FootageError(f"shot {shot!r} is not in shotlist.json (write it as topic-id/shot-id, or give a query)")
    if not isinstance(shot, dict):
        raise FootageError(f"shot must be a library query such as {{\"activity\": [\"chase\"]}}, or a shot-list id")
    try:
        library.check_query(shot)
    except library.LibraryError as e:
        raise FootageError(f"shot {json.dumps(shot)}: {e}") from None
    return dict(shot)


def wants_stars(query):
    """True for a beat about wanted stars: it asks for a star rise, or for footage with stars showing."""
    return bool(query.get("stars_rise")) or (query.get("min_stars") or 0) >= 1


def candidates(indexes, query, profile=None):
    """Every piece of footage a query could be filled from, as library rows: the segments it matches and, for a
    beat about wanted stars, the star rises it matches (unless the query itself says stars_rise: false)."""
    base = dict(query)
    if profile and not base.get("profile"):
        base["profile"] = profile
    rows = []
    try:
        if wants_stars(base) and base.get("stars_rise") is not False:
            rise = {k: v for k, v in base.items() if k not in ("min_len", "max_len")}
            rows += library.find(indexes, **{**rise, "stars_rise": True})
        if not base.get("stars_rise"):
            rows += library.find(indexes, **{k: v for k, v in base.items() if k not in ("stars_rise", "before_s", "after_s")})
    except library.LibraryError as e:
        raise FootageError(str(e)) from None
    return [r for r in rows if not index.off_limits(Path(r["path"]))]


def score(cand, used_videos, used_segments, stars_beat):
    """How good a candidate is for a beat; higher is better. More action, a star rise for a beat about wanted
    stars, and a recording (and above all a segment) this Short has not shown yet."""
    points = ACTION.get(cand["activity"], 0.5) + 0.5 * cand["max_stars"] + (0.5 if cand["police_visible"] else 0.0)
    points += cand.get("confidence") or 0.0
    points += min(len(cand.get("text_hits") or []), 3) * 0.3
    if "change_s" in cand and stars_beat:
        points += 3.0 + 0.5 * (cand["stars_after"] - cand["stars_before"])
    points -= 2.0 * used_videos.get(cand["video"], 0)
    points -= 4.0 * used_segments.get((cand["video"], cand["segment"]), 0)
    return points


def why_text(cand, used_videos, start, seconds):
    """One plain line for picks.json: what the footage is and why it won."""
    what = [cand["activity"].replace("_", " "), f"{cand['max_stars']} wanted star(s)" if cand["max_stars"] else "no stars"]
    if cand["police_visible"]:
        what.append("police in view")
    if "change_s" in cand:
        what.append(f"the stars go from {cand['stars_before']} to {cand['stars_after']} "
                    f"{cand['change_s'] - start:.1f} s into the beat (HUD read {cand['hud_start']['stars']} before, "
                    f"{cand['hud_end']['stars']} after)")
    if cand.get("text_hits"):
        what.append(f"the index saw: {cand['text_hits'][0]['text']}")
    elif cand.get("notable"):
        what.append(f"the index saw: {cand['notable'].split(' | ')[0]}")
    what.append("first footage from this recording" if not used_videos.get(cand["video"])
                else f"recording already used {used_videos[cand['video']]} time(s)")
    return "; ".join(what)


def pick_beats(beats, indexes, profile=None, avoid=(), shotlist=None):
    """Footage for every "shot" beat. beats: [{"line", "shot" (both from 0), "query": the beat's shot, "seconds",
    "lead", "tail"}] in the Short's order. avoid: (video, start, end) spans earlier Shorts showed; they are left
    alone while anything else fits. Returns one pick per beat, in order:
      {"line", "shot", "found": True, "video", "path", "start_s", "end_s", "seconds", "kind": "segment" or "rise",
       "segment", "activity", "stars", "why", "reused": True if it had to take footage an earlier Short showed}
    or {"line", "shot", "found": False, "why": what was looked for and why nothing fitted}."""
    by_stem = {idx["video"]["stem"]: idx for idx in indexes}
    taken, used_videos, used_segments, picks = [], {}, {}, []
    for beat in beats:
        where = {"line": beat["line"], "shot": beat["shot"]}
        query = shot_query(beat["query"], shotlist)
        rows = candidates(indexes, query, profile)
        if not rows:
            picks.append({**where, "found": False, "why": f"no footage in the library matches {json.dumps(query)}"})
            continue
        stars_beat = wants_stars(query)
        ranked = sorted(rows, key=lambda c: (-score(c, used_videos, used_segments, stars_beat), c["video"], c["start_s"]))
        chosen = None
        # First without the footage earlier Shorts showed; if nothing fits, with it.
        for blocked in ([*taken, *avoid], taken) if avoid else (taken,):
            for cand in ranked:
                start = place(by_stem[cand["video"]], cand, beat["seconds"], beat.get("lead", 0.0), beat.get("tail"),
                              blocked)
                if start is not None:
                    chosen = (cand, start)
                    break
            if chosen:
                break
        if not chosen:
            picks.append({**where, "found": False,
                          "why": f"{len(rows)} piece(s) of footage match {json.dumps(query)}, but none has "
                                 f"{beat['seconds']:.1f} s of clean footage left that this Short doesn't already show"})
            continue
        cand, start = chosen
        reused = overlaps(cand["video"], start - beat.get("lead", 0.0), start + max(beat["seconds"], beat.get("tail") or 0),
                          avoid)
        end = start + beat["seconds"]
        picks.append({**where, "found": True, "video": cand["video"], "path": cand["path"], "profile": cand["profile"],
                      "start_s": round(start, 3), "end_s": round(end, 3), "seconds": round(beat["seconds"], 3),
                      "kind": "rise" if "change_s" in cand else "segment", "segment": cand["segment"],
                      "activity": cand["activity"], "stars": cand["max_stars"],
                      "change_s": cand.get("change_s"), "reused": reused,
                      "why": why_text(cand, used_videos, start, beat["seconds"])
                             + ("; an earlier Short showed this footage too (nothing else fitted)" if reused else "")})
        taken.append((cand["video"], start - beat.get("lead", 0.0), start + max(beat["seconds"], beat.get("tail") or 0.0)))
        used_videos[cand["video"]] = used_videos.get(cand["video"], 0) + 1
        used_segments[(cand["video"], cand["segment"])] = used_segments.get((cand["video"], cand["segment"]), 0) + 1
    return picks


# --- The script ---

def shots_of(line):
    raw = line.get("beats", line.get("visuals")) if isinstance(line, dict) else None
    return raw if isinstance(raw, list) else []


def shot_beats(script):
    """(line, shot, beat) from 0, for every beat that asks for footage with "shot"."""
    lines = script.get("lines") if isinstance(script.get("lines"), list) else []
    return [(n, i, beat) for n, line in enumerate(lines) for i, beat in enumerate(shots_of(line))
            if isinstance(beat, dict) and "shot" in beat]


def script_problems(script, shotlist=None):
    """What is wrong with the footage settings and the "shot" beats themselves, before anything is looked up."""
    problems = []
    footage = script.get("footage", {})
    if not isinstance(footage, dict) or any(k not in FOOTAGE_KEYS for k in footage):
        problems.append(f"footage is an object with {', '.join(FOOTAGE_KEYS)} (all optional)")
        footage = {}
    if not isinstance(footage.get("game_audio", True), bool):
        problems.append("footage.game_audio is true (the game's own sound stays in, under the voice) or false")
    for n, i, beat in shot_beats(script):
        where = f"line {n + 1}, shot {i + 1}"
        if beat.get("beat") not in SHOT_BEATS:
            problems.append(f"{where}: footage fills a full, card or banner beat; give it \"beat\": \"full\" (the whole "
                            "screen) or \"card\" (a framed card, so the HUD stays in view)")
        if "file" in beat or "card" in beat:
            problems.append(f"{where}: a beat has a shot (footage from the library) or a file or card text, one of them")
        try:
            shot_query(beat["shot"], shotlist)
        except FootageError as e:
            problems.append(f"{where}: {e}")
    if not shot_beats(script):
        problems.append("no beat asks for footage: give at least one beat a \"shot\" (a library query), or use "
                        "make_short.py for a Short made of pictures")
    return problems


def fallback_query(beat, shotlist=None):
    """The picture a beat asks Rockstar's media for when no footage fits: its own "query", else words for what the
    shot was after."""
    if isinstance(beat.get("query"), str) and beat["query"].strip():
        return beat["query"]
    query = shot_query(beat["shot"], shotlist)
    words = [FALLBACK_WORDS[a] for a in query.get("activity") or [] if a in FALLBACK_WORDS]
    if wants_stars(query):
        words.append("police car")
    return " ".join(dict.fromkeys(" ".join(words or ["city street car"]).split()))


def with_footage(script, picks_by_beat, placeholder=None, shotlist=None):
    """The script with every "shot" beat turned into a beat explainer.py and picker.py know: the picked footage as
    file + at (+ sound), or, where nothing was found, the Rockstar fallback query. With placeholder (a video path)
    every shot beat gets that file instead: only for measuring the beats' lengths, which don't depend on the file."""
    out = json.loads(json.dumps(script))
    sound = bool((script.get("footage") or {}).get("game_audio", True))
    for n, i, beat in shot_beats(out):
        pick = picks_by_beat.get((n, i))
        fallback = fallback_query(beat, shotlist)
        asked = {k: beat[k] for k in picker.QUERY_KEYS if k in beat}     # the beat's own fallback settings
        for key in ("shot", *picker.QUERY_KEYS):
            beat.pop(key, None)
        if placeholder:
            beat.update(file=str(placeholder), at="0")
        elif pick and pick["found"]:
            beat.update(file=pick["path"], at=f"{pick['start_s']:.3f}")
            if sound:
                beat["sound"] = True
        else:
            beat.update(asked)
            beat["query"] = fallback
    return out


def for_explainer(resolved, script):
    """The resolved script with only what explainer.py reads: this tool's and make_short.py's own settings and the
    lines' fact lists are taken out; the footage's volume goes in as game_volume."""
    out = json.loads(json.dumps(resolved))
    footage = out.pop("footage", None) or {}
    for key in make_short.OWN_KEYS:
        out.pop(key, None)
    if isinstance(footage.get("game_volume"), (int, float)) and not isinstance(footage["game_volume"], bool):
        out["game_volume"] = footage["game_volume"]
    for line in out.get("lines") if isinstance(out.get("lines"), list) else []:
        if isinstance(line, dict):
            line.pop("facts", None)
    return out


# --- Earlier Shorts' footage ---

def load_used(shorts_dir):
    data = index.read_json(Path(shorts_dir) / USED_FOOTAGE)
    return data if isinstance(data, dict) and isinstance(data.get("shorts"), list) else {"shorts": []}


def recent_spans(used, name=None, last=RECENT_SHORTS):
    """(video, start, end) of the footage the last few finished Shorts showed, this Short's own earlier render left
    out."""
    shorts = [s for s in used["shorts"] if s.get("name") != name][-last:]
    return [tuple(span) for s in shorts for span in s.get("spans", [])]


def save_used(shorts_dir, name, spans):
    used = load_used(shorts_dir)
    used["shorts"] = [s for s in used["shorts"] if s.get("name") != name] + [{"name": name, "spans": spans}]
    index.write_json(Path(shorts_dir) / USED_FOOTAGE, used)


# --- After the render: what really got on screen ---

def shown_spans(run_log, paths):
    """Every stretch of footage the render put on screen, from its own log: [{"video" (path), "start_s", "end_s",
    "line", "what"}]. That is each footage beat, the loop ending's repeat of the first one, and the footage blurred
    behind a text card. paths: the footage files of this Short."""
    out = []
    for v in run_log.get("visuals") or []:
        named = f"{v.get('source', '')} {v.get('behind') or ''}"
        path = next((p for p in paths if str(p) in named), None)
        if path is None or v.get("source_start_s") is None:
            continue
        what = ("the loop ending" if v.get("loop_tail") else "behind a text card" if v.get("behind") and
                str(path) not in str(v.get("source", "")) else "a beat")
        out.append({"video": str(path), "start_s": v["source_start_s"],
                    "end_s": round(v["source_start_s"] + v["seconds"], 3), "line": v.get("line"), "what": what})
    return out


def unclean(spans, indexes):
    """The shown stretches that are not clean footage, as plain lines (none when all is well)."""
    by_path = {str(Path(idx["video"]["path"])): idx for idx in indexes}
    bad = []
    for s in spans:
        idx = by_path.get(str(Path(s["video"])))
        if idx is None or not any(a <= s["start_s"] + 1e-6 and s["end_s"] <= b + 1e-6 for a, b in usable_spans(idx)):
            bad.append(f"line {s['line']}: {Path(s['video']).name} {s['start_s']:.1f}-{s['end_s']:.1f} s ({s['what']}) "
                       "is not all usable footage: part of it is a cutscene, a menu, the phone or loading")
    return bad


# --- picks.json and picks.html ---

def beat_rows(script, resolved, picks_by_beat, rockstar_picks, lengths):
    """One row per beat of the Short for picks.json: where its picture comes from and why."""
    rows = []
    for n, line in enumerate(script.get("lines", [])):
        for i, beat in enumerate(shots_of(line)):
            seconds = lengths[n][i] if lengths else None
            row = {"line": n + 1, "shot": i + 1, "beat": beat.get("beat") if isinstance(beat, dict) else None,
                   "say": line.get("say", ""), "seconds": round(seconds, 2) if seconds is not None else None}
            pick = picks_by_beat.get((n, i))
            after = shots_of(resolved["lines"][n])[i]
            if pick and pick["found"]:
                row.update(source="footage", video=pick["video"], path=pick["path"], start_s=pick["start_s"],
                           end_s=pick["end_s"], kind=pick["kind"], activity=pick["activity"], stars=pick["stars"],
                           change_s=pick.get("change_s"), why=pick["why"], query=beat["shot"])
            elif pick:
                rock = next((p for p in rockstar_picks if p["line"] == n + 1 and p["shot"] == i + 1), None)
                row.update(source="rockstar fallback", why=pick["why"], query=beat["shot"],
                           fallback_query=after.get("query") or (rock or {}).get("query"),
                           path=(rock or {}).get("path"))
            elif isinstance(after, dict) and "file" in after:
                rock = next((p for p in rockstar_picks if p["line"] == n + 1 and p["shot"] == i + 1), None)
                row.update(source="rockstar picture" if rock and rock.get("file") else "named file",
                           path=after["file"], why="the script asked for this picture"
                           if rock and rock.get("picked") else "the script named this file",
                           query=(rock or {}).get("query"))
            else:
                text = (after.get("card") or after.get("text")) if isinstance(after, dict) else str(after)
                row.update(source="text card", text=text, why="a text card; the picture next to it is blurred behind it")
            rows.append(row)
    return rows


def grab_thumb(source, t, out, root=make_short.GTA_ROOT):
    """A 480 px wide JPEG of a video at t (or of an image); False if ffmpeg can't."""
    path = Path(source) if Path(source).is_absolute() else Path(root) / source
    video = path.suffix.lower() in tag_media.VIDEO_EXTS
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", *(["-ss", f"{t:.3f}"] if video else []), "-i", str(path),
                           "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(out)], capture_output=True)
    return done.returncode == 0 and Path(out).is_file()


def write_picks_page(rows, title, out_dir, thumb=grab_thumb):
    """picks.html in the Short's folder: one card per beat, in order, with a thumbnail (the middle of the footage the
    beat shows, or the moment just after a star change), what the voice says over it, where it comes from and why."""
    thumbs = Path(out_dir) / "picks"
    thumbs.mkdir(exist_ok=True)
    cards = []
    for k, r in enumerate(rows, 1):
        name, picture = f"beat_{k:02d}.jpg", ""
        if r.get("path"):
            t = ((r["change_s"] + 1.0) if r.get("change_s") is not None else (r["start_s"] + r["end_s"]) / 2
                 ) if r["source"] == "footage" else 0.0
            if thumb(r["path"], t, thumbs / name):
                picture = f'<img src="picks/{name}" alt="beat {k}">'
        colour = {"footage": "ok", "rockstar fallback": "warn", "text card": "card"}.get(r["source"], "pic")
        if r["source"] == "footage":
            link = "file:///" + quote(str(Path(r["path"])).replace("\\", "/"), safe="/:") + f"#t={r['start_s']:.1f},{r['end_s']:.1f}"
            where = (f'<b>{html.escape(r["video"])}</b> {library.hms(r["start_s"])} to {library.hms(r["end_s"])} '
                     f'({r["start_s"]:.1f}-{r["end_s"]:.1f} s) &middot; <a href="{html.escape(link)}">play this part</a>')
        elif r.get("path"):
            where = f'<b>{html.escape(Path(r["path"]).name)}</b>'
        else:
            where = f'text card: <b>{html.escape(r.get("text") or "")}</b>'
        seconds = f'{r["seconds"]:.1f} s on screen' if r.get("seconds") is not None else ""
        cards.append(
            f'<article class="{colour}"><div class="pic">{picture or "<span>no picture</span>"}</div><div class="txt">'
            f'<h2>Beat {k} <small>line {r["line"]}, shot {r["shot"]} &middot; {html.escape(str(r["beat"] or "shot"))} '
            f'&middot; {seconds}</small></h2>'
            f'<p class="src">{html.escape(r["source"].upper())}</p><p>{where}</p>'
            f'<p class="say">Voice: &ldquo;{html.escape(r["say"])}&rdquo;</p>'
            f'<p class="why">Why: {html.escape(r.get("why") or "")}</p></div></article>')
    footage = sum(1 for r in rows if r["source"] == "footage")
    fallback = sum(1 for r in rows if r["source"] == "rockstar fallback")
    page = PAGE.replace("{TITLE}", html.escape(title)).replace("{CARDS}", "\n".join(cards)).replace(
        "{COUNTS}", f"{len(rows)} beats: {footage} from your own footage, {fallback} fell back to a Rockstar picture, "
                    f"{len(rows) - footage - fallback} cards or named pictures.")
    (Path(out_dir) / "picks.html").write_text(page, encoding="utf-8")
    return Path(out_dir) / "picks.html"


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Picks: {TITLE}</title>
<style>
body { margin:0; background:#0f1115; color:#e6e8ec; font:15px/1.5 system-ui, "Segoe UI", sans-serif; }
main { max-width:1100px; margin:0 auto; padding:16px; }
h1 { font-size:22px; margin:0 0 4px; } .lead { color:#9aa3b2; margin:0 0 16px; }
article { display:grid; grid-template-columns:480px 1fr; gap:16px; background:#171a21; border:1px solid #2a2f3a;
  border-left:6px solid #64748b; border-radius:6px; padding:12px; margin:0 0 14px; }
article.ok { border-left-color:#22c55e; } article.warn { border-left-color:#f59e0b; } article.card { border-left-color:#38bdf8; }
.pic img { width:100%; height:auto; display:block; border-radius:4px; background:#000; }
.pic span { display:grid; place-items:center; height:140px; color:#9aa3b2; background:#0b0d12; border-radius:4px; }
h2 { font-size:17px; margin:0 0 6px; } h2 small { color:#9aa3b2; font-weight:400; font-size:13px; }
p { margin:4px 0; } .src { font-size:12px; letter-spacing:.06em; color:#9aa3b2; } .say { color:#fde68a; }
.why { color:#c9ced8; } a { color:#93c5fd; }
@media (max-width:900px) { article { grid-template-columns:1fr; } }
</style></head><body><main>
<h1>Picks: {TITLE}</h1>
<p class="lead">{COUNTS} Green edge: your own footage. Orange: no footage fitted, so a Rockstar picture stands in.
Blue: a text card. Check that each picture shows what the voice says over it. "Play this part" opens the recording
at that moment.</p>
{CARDS}
</main></body></html>
"""


# --- The whole run ---

def check_words(script):
    words = make_short.spoken_words(script)
    problems, warnings = [], []
    if not WORDS_LIMIT[0] <= words <= WORDS_LIMIT[1]:
        problems.append(f"the script has {words} spoken words; {WORDS_TARGET[0]}-{WORDS_TARGET[1]} fills a "
                        f"{LENGTH_S[0]:.0f}-35 s footage Short, and outside {WORDS_LIMIT[0]}-{WORDS_LIMIT[1]} it can't fit")
    elif not WORDS_TARGET[0] <= words <= WORDS_TARGET[1]:
        warnings.append(f"the script has {words} spoken words; about {WORDS_TARGET[0]}-{WORDS_TARGET[1]} fills "
                        f"{LENGTH_S[0]:.0f}-35 s")
    return words, problems, warnings


def explainer_timing(path, root):
    import explainer                                   # needs .venv-tts
    try:
        return explainer.timing(path, root)
    except explainer.ScriptError as e:
        raise FootageError(f"explainer.py can't read the script: {e}") from None


def explainer_render(path, out, root, shorts_dir):
    import explainer
    try:
        explainer.render(path, root, shorts_dir, run_dir=out)
    except explainer.ScriptError as e:
        raise FootageError(f"explainer.py can't render it: {e}") from None
    return explainer.pacing_lines


def footage_short(script_path, check_only=False, shorts_dir=SHORTS_DIR, root=make_short.GTA_ROOT, today=None,
                  facts=None, tags=None, indexes=None, timing=None, render=None, log=print, allow_stale=False,
                  folder_date=None, allow_weak=False, shotlist=None, thumb=grab_thumb, library_dir=index.LIBRARY):
    """Check a script, pick its footage and make its Short. Returns (folder or None, ready). FootageError lists what
    stops it. facts, tags, indexes, timing, render and thumb are swapped for stand-ins by the tests."""
    script = make_short.read_script(script_path)
    today = today or date.today().isoformat()
    name = f"{folder_date or today}-{make_short.slug_of(str(script.get('title', '')))}"
    try:
        facts = facts if facts is not None else facts_file.load_facts()
        tags = tags if tags is not None else tag_media.load_tags()
        indexes = indexes if indexes is not None else library.load_library(library_dir)
    except (facts_file.FactsError, tag_media.TagError, library.LibraryError) as e:
        raise FootageError(str(e)) from None

    # 1. Everything that needs no footage and no voice.
    words, problems, warnings = check_words(script)
    problems = make_short.title_problems(script.get("title")) + make_short.upload_problems(script) + problems
    problems += script_problems(script, shotlist)
    fact_problems, fact_warnings, claims = facts_file.check_script(script, facts)
    stale = facts_file.stale_facts(claims, today)
    if stale and allow_stale:
        fact_warnings.append("--allow-stale: rendering with " + ", ".join(
            f"{s['fact']['id']} (checked {s['fact'].get('checked_on') or 'never'})" for s in stale)
            + ", which should have been checked again first")
    elif stale:
        fact_problems.append(facts_file.stale_text(stale, today))
    problems += fact_problems
    warnings += fact_warnings
    if problems:
        raise FootageError("the script needs fixing before it can be made:\n  " + "\n  ".join(problems))
    profile = (script.get("footage") or {}).get("profile")
    pool = [idx for idx in indexes if not profile or idx["profile"]["name"] == profile]
    if not pool:
        raise FootageError("the footage library has no recording" + (f" indexed with profile {profile}" if profile else "")
                           + ": run index.py on a recording first")

    out = Path(shorts_dir) / name
    out.mkdir(parents=True, exist_ok=True)
    used_log = Path(shorts_dir) / picker.USED_LOG.name
    recent = picker.recent_files(picker.load_used(used_log), name)

    def resolve(picks_by_beat, placeholder=None):
        """The script explainer.py gets, and the Rockstar picks in it."""
        staged = with_footage(script, picks_by_beat, placeholder, shotlist)
        try:
            resolved, rock, pick_problems, pick_warnings = picker.resolve_script(staged, tags, recent,
                                                                                 allow_weak=allow_weak)
        except picker.PickError as e:
            raise FootageError(str(e)) from None
        if pick_problems:
            raise FootageError("a picture could not be picked:\n  " + "\n  ".join(pick_problems))
        return resolved, rock, pick_warnings

    # 2. The beats' lengths: they depend on the words and the beats, not on which footage fills them.
    skeleton, _, _ = resolve({}, placeholder=pool[0]["video"]["path"])
    timing_path = out / "script_timing.json"
    timing_path.write_text(json.dumps(for_explainer(skeleton, script), indent=2, ensure_ascii=False), encoding="utf-8")
    times = (timing or explainer_timing)(timing_path, root)
    timing_path.unlink()
    lengths = times["lines"]
    if not LENGTH_S[0] <= times["total_s"] <= LENGTH_S[1]:
        rate = words / times["total_s"]
        change = round((min(max(times["total_s"], LENGTH_S[0] + 2), 35.0) - times["total_s"]) * rate)
        raise FootageError(f"the voice comes to {times['total_s']:.1f} s with these {words} words; a footage Short is "
                           f"{LENGTH_S[0]:.0f}-{LENGTH_S[1]:.0f} s. {'Add' if change > 0 else 'Cut'} about {abs(change)} "
                           "words (or change the speed, 1.1 at most). Nothing was picked or rendered.")

    # 3. Footage for every shot beat, cut to its length. A text card blurs the picture next to it for its own time,
    # and the loop ending plays what comes before the first beat, so those stretches must be clean too.
    behind = {}
    for card, pic in times.get("behind", {}).items():
        behind[tuple(pic)] = max(behind.get(tuple(pic), 0.0), lengths[card[0]][card[1]])
    beats = [{"line": n, "shot": i, "query": beat["shot"], "seconds": lengths[n][i],
              "lead": times["loop_s"] if times.get("first") and tuple(times["first"]) == (n, i) else 0.0,
              "tail": max(lengths[n][i], behind.get((n, i), 0.0))}
             for n, i, beat in shot_beats(script) if lengths[n][i] > 0]
    try:
        picks = pick_beats(beats, pool, profile, recent_spans(load_used(shorts_dir), name), shotlist)
    except FootageError as e:
        raise FootageError(f"the footage could not be picked: {e}") from None
    picks_by_beat = {(p["line"], p["shot"]): p for p in picks}
    for n, i, beat in shot_beats(script):
        if (n, i) not in picks_by_beat:
            picks_by_beat[(n, i)] = {"line": n, "shot": i, "found": False,
                                     "why": "this beat gets no time on screen, so no footage was picked for it"}

    # 4. Beats without footage fall back to a Rockstar picture.
    resolved, rock, pick_warnings = resolve(picks_by_beat)
    warnings += pick_warnings
    rows = beat_rows(script, resolved, picks_by_beat, rock, lengths)
    for r in rows:
        if r["source"] == "footage":
            log(f"  line {r['line']}, shot {r['shot']}: {r['video']} {r['start_s']:.1f}-{r['end_s']:.1f} s  ({r['why']})")
        elif r["source"] == "rockstar fallback":
            log(f"  line {r['line']}, shot {r['shot']}: NO FOOTAGE, fell back to a Rockstar picture "
                f"({Path(r.get('path') or '?').name}). {r['why']}")
    for warning in warnings:
        log(f"  WARNING: {warning}")
    fallbacks = [r for r in rows if r["source"] == "rockstar fallback"]
    footage_rows = [r for r in rows if r["source"] == "footage"]
    log(f"  Checks passed: {words} spoken words, about {times['total_s']:.1f} s; {len(footage_rows)} beat(s) from your "
        f"own footage, {len(fallbacks)} fell back to a Rockstar picture")
    resolved_path = out / "script_resolved.json"
    to_render = for_explainer(resolved, script)
    (out / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    resolved_path.write_text(json.dumps(to_render, indent=2, ensure_ascii=False), encoding="utf-8")
    picks_file = {"short": name, "title": script.get("title"), "length_s": round(times["total_s"], 2),
                  "beats_from_footage": len(footage_rows), "beats_fell_back_to_rockstar": len(fallbacks), "beats": rows}
    (out / "picks.json").write_text(json.dumps(picks_file, indent=1, ensure_ascii=False), encoding="utf-8")
    write_picks_page(rows, str(script.get("title", "")), out, thumb)
    if check_only:
        log(f"  Picks: {out / 'picks.html'}")
        return out, True

    # 5. Render, then check what really got on screen.
    (out / "upload.txt").write_text(make_short.upload_text(script), encoding="utf-8")
    (out / "claims.txt").write_text(facts_file.claims_text(script, claims, facts), encoding="utf-8")
    pacing_lines = (render or explainer_render)(resolved_path, out, root, shorts_dir)
    run_log = json.loads((out / "log.json").read_text(encoding="utf-8"))
    pacing = run_log.get("pacing") or {}
    length = pacing.get("length_s", run_log.get("durations", {}).get("video_s", 0.0))
    notes = [w for w in run_log.get("warnings", []) if "target" not in w or "34" not in w]
    (out / "pacing.txt").write_text("\n".join([*(pacing_lines(pacing) if pacing and pacing_lines else []),
                                               *(f"  WARNING: {w}" for w in notes)]) + "\n", encoding="utf-8")
    not_ready = []
    if not LENGTH_S[0] <= length <= LENGTH_S[1]:
        not_ready.append(f"it is {length:.1f} s long; a footage Short is {LENGTH_S[0]:.0f}-{LENGTH_S[1]:.0f} s (cut or "
                         "add words, or change the speed, 1.1 at most)")
    spans = shown_spans(run_log, sorted({r["path"] for r in footage_rows}))
    not_ready += unclean(spans, pool)
    on_screen = {(s["line"], str(Path(s["video"]))) for s in spans if s["what"] == "a beat"}
    for r in footage_rows:
        if (r["line"], str(Path(r["path"]))) not in on_screen:
            not_ready.append(f"line {r['line']}, shot {r['shot']}: its footage never gets on screen; give its line "
                             "more time or take the beat out")
    picks_file.update(length_s=round(length, 2), shown=spans, ready=not not_ready,
                      game_audio=run_log.get("game_audio"))
    (out / "picks.json").write_text(json.dumps(picks_file, indent=1, ensure_ascii=False), encoding="utf-8")
    ready = not not_ready
    if ready:
        save_used(shorts_dir, name, [[Path(s["video"]).stem, s["start_s"], s["end_s"]] for s in spans])
        picker.save_used(name, [p["file"] for p in rock if p.get("file")], used_log)
    log(f"Folder: {out}")
    for item in ("short.mp4", "picks.html", "picks.json", "script.json", "upload.txt", "claims.txt", "pacing.txt"):
        log(f"  {out / item}")
    for w in notes:
        log(f"  Note: {w}")
    log("READY: look at picks.html and claims.txt, then upload short.mp4 with upload.txt yourself." if ready else
        "NOT READY: " + "; ".join(not_ready))
    return out, ready


def main():
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Check a script and build its Short from your own gameplay footage.")
    parser.add_argument("script", type=Path)
    parser.add_argument("--check", action="store_true",
                        help="check the script and pick the footage (picks.html), but render nothing")
    parser.add_argument("--allow-weak", action="store_true",
                        help="for a Rockstar fallback picture: when nothing matches well, use the closest one")
    parser.add_argument("--allow-stale", action="store_true",
                        help="render even if the script uses a fact that changes from day to day and was checked "
                             "more than 3 days ago (for the owner; the facts are listed as a warning)")
    parser.add_argument("--folder-date", metavar="YYYY-MM-DD",
                        help="the date in the Short's folder name (default: today)")
    parser.add_argument("--library", type=Path, default=index.LIBRARY, help=f"footage library (default {index.LIBRARY})")
    args = parser.parse_args()
    if args.folder_date and facts_file.day_of(args.folder_date) is None:
        parser.error("--folder-date must be a day written as YYYY-MM-DD")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ERROR: ffmpeg and ffprobe must be on PATH", file=sys.stderr)
        sys.exit(2)
    try:
        _, ready = footage_short(args.script, args.check, allow_stale=args.allow_stale, folder_date=args.folder_date,
                                 allow_weak=args.allow_weak, library_dir=args.library)
    except FootageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0 if ready else 1)


if __name__ == "__main__":
    main()
