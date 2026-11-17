"""/launch (PR 25): Shorts from the owner's own gameplay with no questions asked. Two stages:

  .venv\\Scripts\\python.exe launch.py prepare [--game gta6] [--videos FILE ...] [--every 6] [--dry-run]
  .venv-tts\\Scripts\\python.exe launch.py render [--run FOLDER] [--short N] [--hook N] [--no-cold-open]

prepare, stopping only when nothing at all can be done:
  1. finds the new recordings in C:\\AI\\gta\\input: not in the footage library yet and not part of an earlier /launch
     run (so a second run on the same night takes only the part-2 files). Held-out footage is never touched. They are
     put in session order by the time in the PS5 file name.
  2. picks the HUD profile: an existing finished one (profiles/<game>.json), else a guided calibration with every
     guess accepted (calibrate_guided.auto_accept) on the longest recording. If that fails, or a profile that failed
     its check before is found, the run goes on without HUD rules (the NO-HUD fallback, below).
  3. indexes every recording with index.py and, alongside the model calls, runs clip.py on each for its measured
     action score (loudness + picture change; clip.py itself is unchanged).
  4. checks the HUD against the model per recording (hud_check). A recording whose HUD reading can't be trusted is
     indexed again with profiles/<game>_nohud.json (a profile that matches no HUD on purpose): its labels then come
     from the model alone and it has no wanted-star counts. The model's answers are cached, so that costs one decode.
  5. mines moments (mine): every 30 s window of clean footage gets points for action, star rises, wanted stars, what
     the player is doing, crashes, explosions and stunts the model saw, police and the model's confidence. The best
     windows that don't overlap become moments.
  6. picks 3 (pick_three): the best moment in each third of the session, a moment that fails the safety checks or
     shows footage an earlier Short showed never counts, a category already picked costs a point.
  7. decides each one's type (choose_types): ACTION CLIP or NARRATED, and writes candidates.json with every piece of
     evidence (E1, E2 ...) that the narration and hooks may point to, and a sheet of frames per moment.
Then Claude writes shorts.json (hooks, script) and runs render (see .claude/commands/launch.md).

--dry-run: a trial on any recordings (--videos, default every recording in the input folder). Its profile,
calibration and library live inside its own folder (the library shares the main library's cached model answers),
and it neither reads nor writes _used_footage.json or _launch_runs.json.

Standard library only for prepare (it runs with .venv); render needs .venv-tts (Kokoro).
"""

import argparse
import concurrent.futures
import csv
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import calibrate as cal
import calibrate_guided as guided
import clip
import footage_short
import index
import library
import vlm

HERE = Path(__file__).resolve().parent
INPUT = Path(r"C:\AI\gta\input")
SHORTS_DIR = footage_short.SHORTS_DIR
RUNS_LOG = "_launch_runs.json"           # in the shorts folder: every /launch run and the recordings it took
GAMES = {"gta6": {"name": "GTA 6", "slots": 6, "hashtag": "#GTA6"},
         "gta5": {"name": "GTA V", "slots": 5, "hashtag": "#GTA5"}}
STEP = clip.BUCKET                       # 0.5 s: the HUD and the action score are per half second
GUARD_S = footage_short.GUARD_S          # footage keeps this far from a never_use part
LOOP_LEAD_S = 2.0                        # a narrated Short's loop ending replays this much from before its start

# --- Is the HUD reading trustworthy? (hud_check) ---
GAMEPLAY = ("driving", "on_foot", "shootout", "chase", "flying", "boat", "swimming")
NO_HUD_LABELS = ("cutscene", "menu_map", "loading")
AGREE_MIN = 0.8          # of the sheets the model calls gameplay, at least this share must show the minimap
AGREE_MIN_SHEETS = 10    # ...checked once there are this many
HIDDEN_MIN = 0.5         # of the sheets it calls a cutscene, menu or loading, at least this share must show none
HIDDEN_MIN_SHEETS = 5
FLICKER_PER_MIN = 6.0    # more wanted-star changes a minute than this is a misread, not a wanted level
SURE = 0.6               # a model answer with lower confidence is not used in the check

# --- Moments (mine) ---
WINDOW_S = 30.0          # moments are scored as windows of this length (shorter where clean footage is shorter)
MIN_WINDOW_S = 15.0
WINDOW_STEP_S = 2.0
APART_S = 5.0            # two moments of one recording keep this far apart
MOMENTS_PER_10_MIN = 3   # moments kept per 10 min of a recording (at least 4)
ACT_SCALE = 1.5          # action points: the best smoothed action score in the window / this, 0 to 3
ACT_MAX = 3.0
# What the vision model's notable lines can show, the words for it, and its points.
EVENTS = {
    "crash": (1.5, ("crash", "collision", "collide", "smash", "wreck", "slams into", "rams", "flipped over",
                    "rolls over", "hits a", "hits the", "hit by")),
    "explosion": (1.5, ("explosion", "explode", "blast", "fireball", "on fire", "burning", "flames")),
    "stunt": (1.5, ("jump", "airborne", "mid-air", "midair", "in the air", "flip", "ramp", "stunt", "wheelie")),
    "character": (0.5, ("two characters", "man and woman", "woman and man", "couple", "jason", "lucia", "hug",
                        "kiss", "dancing", "talking", "conversation")),
}
POLICE_WORDS = ("police", "cop", "officer", "patrol car", "squad car", "cruiser")
POLICE_PTS = 0.5
RISE_PTS, RISE_PER_STAR, RISE_MAX = 2.0, 0.5, 4.0
STAR_PTS, STAR_MAX = 0.5, 2.5
CONF_PTS = 0.5
CATEGORY_COST = 1.0      # pick_three: a category already picked costs this much
MIN_APART_S = 300.0      # a pick from outside its third keeps this far (session time) from the others

# --- Types (choose_types) ---
ACTION_LEN = (15.0, 30.0)
ACTION_BEFORE, ACTION_AFTER = 8.0, 12.0  # an action clip around its moment's anchor, before stretching
ACTION_STRONG = 2.5      # action points that carry an ACTION CLIP on their own
NARRATED_MIN_S = 24.0    # clean footage a NARRATED Short needs (20-35 s of voice, plus room)
NARRATED_MAX_S = 35.0
NARRATED_ITEMS = 3       # different things the footage shows, at least, for a narrated Short
NARRATED_LEAD_S = 3.0    # a narrated Short starts this long before its moment's window


class LaunchError(Exception):
    """/launch can't go on; the text says why and what to do."""


def hms(t):
    m, s = divmod(int(t), 60)
    return f"{m}:{s:02d}"


def read_json(path):
    return index.read_json(path)


def write_json(path, data):
    index.write_json(Path(path), data)


# --- 1. The recordings ---

def recording_time(path):
    """When a recording started: the PS5 writes it into the name (Grand Theft Auto V_20261026160754); else the
    file's modified time."""
    found = re.search(r"(20\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", Path(path).stem)
    if found:
        try:
            return datetime(*map(int, found.groups()))
        except ValueError:
            pass
    return datetime.fromtimestamp(Path(path).stat().st_mtime)


def load_runs(shorts_dir):
    data = read_json(Path(shorts_dir) / RUNS_LOG)
    return data if isinstance(data, dict) and isinstance(data.get("runs"), list) else {"runs": []}


def find_recordings(input_dir, library_dir, runs, explicit=None):
    """(the recordings to use, in session order, {name: why it was skipped}). explicit: the --videos list, used as
    given. Otherwise every video in input_dir that is not in the library yet and not part of an earlier run. A run
    still "preparing" (it stopped) keeps its own recordings: they are taken again."""
    skipped = {}
    if explicit:
        videos = [Path(v) for v in explicit]
        missing = [str(v) for v in videos if not v.is_file()]
        if missing:
            raise LaunchError("not found: " + ", ".join(missing))
    else:
        unfinished = [r for r in runs["runs"] if r.get("status") == "preparing"]
        if unfinished:
            videos = [Path(p) for p in unfinished[-1]["recordings"] if Path(p).is_file()]
        else:
            lib = read_json(Path(library_dir) / "library.json") or {}
            indexed = set((lib.get("videos") or {}).keys())
            earlier = {Path(p).stem for r in runs["runs"] for p in r.get("recordings", [])}
            videos = []
            for v in sorted(Path(input_dir).iterdir()) if Path(input_dir).is_dir() else []:
                if not v.is_file() or v.suffix.lower() not in index.VIDEO_EXTS:
                    continue
                if v.stem in earlier:
                    skipped[v.name] = "an earlier /launch run took it"
                elif v.stem in indexed:
                    skipped[v.name] = "already in the footage library"
                else:
                    videos.append(v)
    out = []
    for v in videos:
        barred = index.off_limits(v)
        if barred:
            skipped[v.name] = f"held-out footage ({barred}) is never used"
        else:
            out.append(v)
    return sorted(out, key=lambda v: (recording_time(v), v.name)), skipped


def run_folder(shorts_dir, day, runs, dry_run=False):
    """The run's folder: launch-<day>, then launch-<day>-part2, -part3 ... for later runs the same night. A run that
    stopped while preparing is carried on in its own folder. A dry run gets launch-<day>-dryrun."""
    shorts_dir = Path(shorts_dir)
    if dry_run:
        return shorts_dir / f"launch-{day}-dryrun"
    unfinished = [r for r in runs["runs"] if r.get("status") == "preparing"]
    if unfinished:
        return Path(unfinished[-1]["folder"])
    taken = {Path(r["folder"]).name for r in runs["runs"]}
    for k in range(1, 100):
        name = f"launch-{day}" if k == 1 else f"launch-{day}-part{k}"
        if name not in taken and not (shorts_dir / name / "candidates.json").exists():
            return shorts_dir / name
    raise LaunchError(f"100 launch folders for {day} already: clean up {shorts_dir}")


def note_run(shorts_dir, folder, recordings, status, dry_run=False):
    """Record the run in _launch_runs.json (never in a dry run)."""
    if dry_run:
        return
    runs = load_runs(shorts_dir)
    entry = next((r for r in runs["runs"] if Path(r["folder"]) == Path(folder)), None)
    if entry is None:
        entry = {"folder": str(folder), "started": datetime.now().isoformat(timespec="seconds")}
        runs["runs"].append(entry)
    entry.update(recordings=[str(Path(v)) for v in recordings], status=status,
                 updated=datetime.now().isoformat(timespec="seconds"))
    write_json(Path(shorts_dir) / RUNS_LOG, runs)


# --- 2. The HUD profile ---

def last_guided_result(name):
    data = read_json(cal.CALIB_ROOT / name / "guided_result.json")
    return data.get("result") if isinstance(data, dict) else None


def choose_profile(name, game, recordings, durations, slots, log=print, guided_run=None, tries=3):
    """The profile to index with: {"profile", "mode": "hud" or "nohud", "stars": True/False, "why", "attempts"}.

    - profiles/<name>.json exists, its stars are done, and its last guided check (if any) didn't FAIL: use it.
    - else guided calibration with every guess accepted, on the longest recording first. "PASS" ends it; "PASS for
      the minimap. STARS NOT CALIBRATED" tries the next longest recording for the stars (at most `tries` runs), and
      uses the profile without stars if none shows any; FAIL, or a run that can't start or save, means NO-HUD.
    """
    guided_run = guided_run or guided.run
    nohud = f"{game}_nohud"
    pending = guided.pending_parts(name)
    attempts = []
    if pending is not None and "stars" not in pending:
        if last_guided_result(name) == "FAIL":
            return {"profile": nohud, "mode": "nohud", "stars": False, "attempts": attempts,
                    "why": f"profiles/{name}.json failed its guided check before, so its HUD reading isn't trusted"}
        prof = clip.load_profile(name)
        return {"profile": name, "mode": "hud", "stars": prof.stars_on, "attempts": attempts,
                "why": f"profiles/{name}.json already exists" + ("" if prof.stars_on else " (wanted stars off)")}
    for video in sorted(recordings, key=lambda v: -durations.get(Path(v).stem, 0.0))[:tries]:
        log(f"  Guided calibration of {name} on {Path(video).name}, every guess accepted...")
        code = guided_run(name, video, slots=slots, game=GAMES.get(game, {}).get("name"), browser=False,
                          log=lambda m: log("    " + m), serve=guided.auto_accept)
        result = last_guided_result(name) if code != 2 else None
        attempts.append({"video": Path(video).name, "exit": code, "result": result})
        if code != 0 or not result or result.startswith("FAIL") or result.startswith("SAVED"):
            return {"profile": nohud, "mode": "nohud", "stars": False, "attempts": attempts,
                    "why": f"guided calibration on {Path(video).name} " + (f"ended {result}" if result else
                                                                          "could not start or save a profile")}
        if "STARS NOT CALIBRATED" not in result:
            return {"profile": name, "mode": "hud", "stars": True, "attempts": attempts,
                    "why": f"guided calibration PASS on {Path(video).name}"}
    if guided.pending_parts(name) is not None:
        return {"profile": name, "mode": "hud", "stars": False, "attempts": attempts,
                "why": "guided calibration PASS for the minimap; no recording showed wanted stars, so star counts are "
                       "off and narration may not mention stars"}
    return {"profile": nohud, "mode": "nohud", "stars": False, "attempts": attempts, "why": "no recording to calibrate on"}


# --- 3. clip.py's action score ---

def clip_count(duration_s):
    return max(3, math.ceil(duration_s / 360.0))


def run_clip(video, profile, out_root, logs_dir):
    """clip.py on one recording, as it is (a subprocess, so its own rules and output stay exactly as always). Returns
    its run folder, or None if it failed (the log says why)."""
    out_root, logs_dir = Path(out_root), Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    before = set((out_root / Path(video).stem).glob("*")) if (out_root / Path(video).stem).is_dir() else set()
    with open(logs_dir / f"clip_{Path(video).stem}.txt", "w", encoding="utf-8", errors="replace") as fh:
        done = subprocess.run([sys.executable, str(HERE / "clip.py"), str(video), "--profile", profile,
                               "--count", str(clip_count(probe_duration(video))), "--out", str(out_root)],
                              stdout=fh, stderr=subprocess.STDOUT, cwd=HERE)
    new = sorted(set((out_root / Path(video).stem).glob("*")) - before)
    return new[-1] if done.returncode == 0 and new else None


def probe_duration(video):
    done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                          capture_output=True, text=True)
    try:
        return float(done.stdout.strip())
    except ValueError:
        return 0.0


def action_series(run_dir, n):
    """clip.py's action score per half second, without its wanted-star boost (the stars count on their own here):
    its audio and picture z-scores with its own weights, smoothed as it smooths them. Zeros when there is no run."""
    if run_dir is None or not (Path(run_dir) / "timeline.csv").is_file():
        return [0.0] * n
    scores = read_json(Path(run_dir) / "scores.json") or {}
    weights = scores.get("scoring") or {}
    wa, wv = weights.get("audio_weight", clip.AUDIO_WEIGHT), weights.get("visual_weight", clip.VISUAL_WEIGHT)
    with open(Path(run_dir) / "timeline.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    raw = [wa * float(r["audio_z"]) + wv * float(r["visual_z"]) for r in rows][:n]
    raw += [0.0] * (n - len(raw))
    return clip.gaussian_smooth(raw, clip.SMOOTH_SIGMA / STEP)


# --- 4. Can the HUD reading be trusted? ---

def hud_check(idx, tl):
    """(ok, reasons, numbers) for one recording indexed with a calibrated profile. The HUD and the vision model
    are two separate readings; on a profile that reads the HUD right they agree:
      - the HUD rules are on (the minimap shows in the profile's min_share of the samples: index.py's own test);
      - of the sheets the model calls gameplay (sure of it), AGREE_MIN or more show the minimap;
      - of the sheets it calls a cutscene, menu or loading screen, HIDDEN_MIN or more show none;
      - the wanted-star count changes no more than FLICKER_PER_MIN times a minute."""
    every = (idx.get("settings") or {}).get("every_s") or index.EVERY_S
    samples = tl.get("samples") or []
    reasons, numbers = [], {"minimap_share": tl.get("minimap_share")}
    if not tl.get("on"):
        reasons.append(f"the minimap was found in only {tl.get('minimap_share', 0):.0%} of the recording")

    def shown(t):
        near = [s[2] for s in samples if t <= s[0] < t + every]
        return sum(1 for x in near if x) / len(near) >= 0.5 if near else None

    sheets = [s for s in idx.get("sheets", []) if s.get("activity") and (s.get("confidence") or 0) >= SURE]
    play = [shown(s["t"]) for s in sheets if s["activity"] in GAMEPLAY]
    play = [x for x in play if x is not None]
    hidden = [shown(s["t"]) for s in sheets if s["activity"] in NO_HUD_LABELS]
    hidden = [x for x in hidden if x is not None]
    if play:
        numbers["gameplay_with_minimap"] = round(sum(play) / len(play), 3)
        if len(play) >= AGREE_MIN_SHEETS and numbers["gameplay_with_minimap"] < AGREE_MIN:
            reasons.append(f"the minimap is missing on {1 - numbers['gameplay_with_minimap']:.0%} of the sheets the "
                           f"model calls gameplay (at most {1 - AGREE_MIN:.0%})")
    if hidden:
        numbers["cutscene_without_minimap"] = round(1 - sum(hidden) / len(hidden), 3)
        if len(hidden) >= HIDDEN_MIN_SHEETS and numbers["cutscene_without_minimap"] < HIDDEN_MIN:
            reasons.append(f"the minimap is 'seen' on {sum(hidden) / len(hidden):.0%} of the sheets the model calls a "
                           "cutscene, menu or loading screen")
    minutes = sum(1 for v in tl.get("no_minimap", []) if not v) * STEP / 60.0
    changes = len(library.star_changes(tl.get("count", []))) if tl.get("count") else 0
    numbers["star_changes_per_min"] = round(changes / minutes, 2) if minutes else 0.0
    if minutes and numbers["star_changes_per_min"] > FLICKER_PER_MIN:
        reasons.append(f"the wanted-star count changes {numbers['star_changes_per_min']:g} times a minute (a misread; "
                       f"at most {FLICKER_PER_MIN:g})")
    return not reasons, reasons, numbers


# --- 5. Moments ---

def event_kinds(text):
    """The EVENTS (and "police") a notable line shows, read only where it isn't denied ("no crash")."""
    kept = library.positive_text(text or "")
    kinds = {k for k, (_, words) in EVENTS.items() if any(w in kept for w in words)}
    if any(w in kept for w in POLICE_WORDS):
        kinds.add("police")
    return kinds


def recording_data(stem, idx, act, mode, offset_s):
    """Everything mine() reads about one recording, in one place."""
    n = math.ceil(idx["video"]["duration_s"] / STEP)
    tl = None
    if mode == "hud":
        try:
            tl = library.timeline_of(idx)
        except library.LibraryError:
            tl = None
    count = (tl or {}).get("count") or [0] * n
    rises = library.rise_windows(idx, tl) if tl else []
    every = (idx.get("settings") or {}).get("every_s") or index.EVERY_S
    sheets = [dict(s, kinds=sorted(event_kinds(s.get("notable")))) for s in idx.get("sheets", []) if s.get("notable")]
    return {"stem": stem, "path": idx["video"]["path"], "duration_s": idx["video"]["duration_s"],
            "offset_s": offset_s, "mode": mode, "idx": idx, "n": n, "act": list(act[:n]) + [0.0] * (n - len(act)),
            "count": count, "rises": rises, "every": every, "sheets": sheets, "profile": idx["profile"]["name"],
            "spans": footage_short.usable_spans(idx)}


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def score_window(rec, a, b):
    """(points, terms) for footage a..b seconds of one recording. Every term is kept, so the review page and
    candidates.json can say why a moment won."""
    i0, i1 = int(a / STEP), max(int(a / STEP) + 1, min(rec["n"], int(math.ceil(b / STEP))))
    terms = {}
    terms["action"] = round(min(ACT_MAX, max(0.0, max(rec["act"][i0:i1], default=0.0) / ACT_SCALE)), 2)
    rises = [r for r in rec["rises"] if a + 1.5 <= r["change_s"] <= b - 2.0]
    if rises:
        terms["star_rise"] = round(min(RISE_MAX, sum(RISE_PTS + RISE_PER_STAR * (r["stars_after"] - r["stars_before"])
                                                     for r in rises)), 2)
    top = max(rec["count"][i0:i1], default=0)
    if top:
        terms["stars"] = min(STAR_MAX, STAR_PTS * top)
    segs = [s for s in rec["idx"]["segments"] if not s["never_use"]
            and overlap(a, b, s["start_s"], s["end_s"]) >= min(4.0, s["duration_s"])]
    best = max((footage_short.ACTION.get(s["activity"], 0.5) - 1.0 for s in segs), default=0.0)
    if best > 0:
        terms["activity"] = round(best, 2)
        terms["activity_label"] = max(segs, key=lambda s: footage_short.ACTION.get(s["activity"], 0.5))["activity"]
    sheets = [s for s in rec["sheets"] if overlap(a, b, s["t"], s["t"] + rec["every"]) >= min(3.0, rec["every"] / 2)]
    kinds = {k for s in sheets for k in s["kinds"]}
    for k in EVENTS:
        if k in kinds:
            terms[k] = EVENTS[k][0]
    if "police" in kinds or any(s["police_visible"] for s in segs) or any(s.get("police_visible") for s in sheets):
        terms["police"] = POLICE_PTS
    confs = [s.get("confidence") or 0.0 for s in sheets]
    if confs:
        terms["confidence"] = round(CONF_PTS * sum(confs) / len(confs), 2)
    points = round(sum(v for k, v in terms.items() if isinstance(v, (int, float))), 3)
    return points, terms


def category_of(terms):
    """What a moment is mostly about, for spreading the picks over different kinds of moment."""
    ranked = [("star rise", terms.get("star_rise", 0)), ("crash", terms.get("crash", 0)),
              ("explosion", terms.get("explosion", 0)), ("stunt", terms.get("stunt", 0)),
              (terms.get("activity_label", "action"), terms.get("activity", 0)),
              ("character", terms.get("character", 0)), ("action", terms.get("action", 0) / 2)]
    best = max(ranked, key=lambda kv: kv[1])
    return best[0] if best[1] > 0 else "action"


def windows(rec):
    """Every window a moment may be scored on: inside one clean stretch, GUARD_S from its ends, WINDOW_S long (or
    the whole stretch when that is MIN_WINDOW_S to WINDOW_S), one every WINDOW_STEP_S."""
    out = []
    for a, b in rec["spans"]:
        lo, hi = a + GUARD_S, b - GUARD_S
        if hi - lo < MIN_WINDOW_S:
            continue
        length = min(WINDOW_S, hi - lo)
        t = lo
        while t + length <= hi + 1e-6:
            out.append((round(t, 2), round(t + length, 2), (a, b)))
            t += WINDOW_STEP_S
    return out


def mine(rec, used=()):
    """The moments of one recording: the best windows that don't overlap each other or footage an earlier Short
    showed (used: [(video stem, start, end)]), best first."""
    scored = []
    for a, b, span in windows(rec):
        if any(v == rec["stem"] and a < e and s < b for v, s, e in used):
            continue
        points, terms = score_window(rec, a, b)
        scored.append((points, a, b, span, terms))
    scored.sort(key=lambda x: (-x[0], x[1]))
    keep = max(4, math.ceil(rec["duration_s"] / 600.0 * MOMENTS_PER_10_MIN))
    moments = []
    for points, a, b, span, terms in scored:
        if any(a < m["end_s"] + APART_S and m["start_s"] < b + APART_S for m in moments):
            continue
        moments.append({"video": rec["stem"], "path": rec["path"], "profile": rec["profile"], "mode": rec["mode"],
                        "start_s": a, "end_s": b, "span": [round(span[0], 2), round(span[1], 2)],
                        "session_s": round(rec["offset_s"] + (a + b) / 2, 1), "score": points, "terms": terms,
                        "category": category_of(terms)})
        if len(moments) >= keep:
            break
    return moments


def pick_three(moments, session_s, ok=lambda m: (True, []), count=3, log=print):
    """count moments: the best one in each third of the session (a category already picked costs CATEGORY_COST),
    then, for an empty third, the best moment left at least MIN_APART_S from the picks. ok(moment) -> (passes,
    reasons) is the safety check; it runs only on moments that would be picked, best first, and a moment that fails
    it is skipped (the reasons are kept in "rejected")."""
    picked, rejected, checked = [], [], {}

    def adjusted(m):
        return m["score"] - CATEGORY_COST * sum(1 for p in picked if p["category"] == m["category"])

    def first_ok(pool):
        for m in sorted(pool, key=lambda m: (-adjusted(m), m["session_s"])):
            key = (m["video"], m["start_s"])
            if key not in checked:
                checked[key] = ok(m)
                if not checked[key][0]:
                    rejected.append({"video": m["video"], "start_s": m["start_s"], "end_s": m["end_s"],
                                     "score": m["score"], "why": checked[key][1]})
                    log(f"  {m['video']} {hms(m['start_s'])}-{hms(m['end_s'])} failed the safety checks: "
                        + "; ".join(checked[key][1]))
            if checked[key][0]:
                return m
        return None

    third = max(session_s, 1e-6) / count
    for k in range(count):
        m = first_ok([m for m in moments if min(count - 1, int(m["session_s"] / third)) == k and m not in picked])
        if m:
            picked.append(m)
    while len(picked) < count:
        m = first_ok([m for m in moments if m not in picked
                      and all(abs(m["session_s"] - p["session_s"]) >= MIN_APART_S for p in picked)])
        if not m:
            break
        picked.append(m)
    for m in picked:
        m["third"] = min(count - 1, int(m["session_s"] / third)) + 1
    return sorted(picked, key=lambda m: -m["score"]), rejected


# --- 6. Evidence and types ---

def sheet_picture(rec, t):
    idx_dir = Path(rec["idx"].get("_dir") or "")
    path = idx_dir / "frames" / index.frame_name(t)
    return str(path) if path.is_file() else None


def evidence(rec, a, b):
    """Everything the footage a..b shows that a line may point to, as items with an id (E1 ...), a kind, the times
    in the recording, the numbers it supports and where it comes from. Kinds:
      activity  an index segment's label (chase, shootout, driving ...), with its length in seconds
      stars     a wanted-star rise (HUD, the exact half second) or the stars showing (HUD count); never in NO-HUD mode
      police    police seen by the model
      event     crash, explosion, stunt or character, from a notable line
      seen      the model's notable line for one sheet (6 s), word for word"""
    items = []
    for k, s in enumerate(rec["idx"]["segments"], 1):
        lo, hi = max(a, s["start_s"]), min(b, s["end_s"])
        if s["never_use"] or hi - lo < 1.0:
            continue
        items.append({"kind": "activity", "value": s["activity"], "from_s": round(lo, 2), "to_s": round(hi, 2),
                      "numbers": [round(hi - lo)], "source": f"index segment {k} ({s['decided_by']})"})
        if s["police_visible"]:
            items.append({"kind": "police", "value": "police seen", "from_s": round(lo, 2), "to_s": round(hi, 2),
                          "numbers": [], "source": f"index segment {k}: the model saw police"})
    if rec["mode"] == "hud":
        for r in rec["rises"]:
            if a <= r["change_s"] <= b:
                items.append({"kind": "stars", "value": f"wanted stars {r['stars_before']} to {r['stars_after']}",
                              "from_s": r["change_s"], "to_s": r["change_s"], "at_s": r["change_s"],
                              "numbers": [r["stars_before"], r["stars_after"]],
                              "source": "HUD star count (the change, read twice a second)"})
        i = int(a / STEP)
        while i < min(rec["n"], int(math.ceil(b / STEP))):
            c, j = rec["count"][i], i
            while j < min(rec["n"], int(math.ceil(b / STEP))) and rec["count"][j] == c:
                j += 1
            if c and (j - i) * STEP >= 2.0:
                items.append({"kind": "stars", "value": f"{c} wanted star{'s' if c > 1 else ''} showing",
                              "from_s": round(i * STEP, 2), "to_s": round(j * STEP, 2), "numbers": [c],
                              "source": "HUD star count"})
            i = j
    for s in rec["sheets"]:
        lo, hi = max(a, s["t"]), min(b, s["t"] + rec["every"])
        if hi - lo < 1.5:
            continue
        pic = sheet_picture(rec, s["t"])
        for k in s["kinds"]:
            items.append({"kind": "police" if k == "police" else "event", "value": k if k != "police" else "police seen",
                          "from_s": s["t"], "to_s": round(s["t"] + rec["every"], 2), "numbers": [],
                          "source": f"vision model, sheet at {hms(s['t'])}: \"{s['notable']}\"", "picture": pic})
        items.append({"kind": "seen", "value": s["notable"], "from_s": s["t"], "to_s": round(s["t"] + rec["every"], 2),
                      "numbers": [], "source": f"vision model, sheet at {hms(s['t'])} (confidence "
                                                f"{s.get('confidence', 0):.2f})", "picture": pic})
    items.sort(key=lambda e: (e["from_s"], e["kind"]))
    for k, e in enumerate(items, 1):
        e["id"] = f"E{k}"
    return items


def distinct_items(items):
    """How many different things the footage shows: each activity label, a star rise, stars showing, police, each
    event. The model's own lines don't count here (they describe the same things)."""
    keys = set()
    for e in items:
        if e["kind"] == "activity" and e["value"] not in ("driving", "on_foot", "other"):
            keys.add(("activity", e["value"]))
        elif e["kind"] == "stars":
            keys.add(("stars", "rise" if "at_s" in e else "showing"))
        elif e["kind"] in ("police", "event"):
            keys.add((e["kind"], e["value"]))
    return len(keys)


def anchor(rec, m):
    """The moment's main instant: a star rise, else a crash/explosion/stunt sheet's middle, else its best action."""
    a, b = m["start_s"], m["end_s"]
    rises = [r["change_s"] for r in rec["rises"] if a <= r["change_s"] <= b]
    if rises:
        return rises[0]
    hits = [s["t"] + rec["every"] / 2 for s in rec["sheets"] if a <= s["t"] + rec["every"] / 2 <= b
            and set(s["kinds"]) & {"crash", "explosion", "stunt"}]
    if hits:
        return hits[0]
    i0, i1 = int(a / STEP), max(int(a / STEP) + 1, min(rec["n"], int(b / STEP)))
    return max(range(i0, i1), key=lambda i: rec["act"][i]) * STEP


def action_window(rec, m, t):
    """An ACTION CLIP's footage round the anchor t: ACTION_BEFORE before to ACTION_AFTER after, stretched while the
    action score stays up (to 30 s), kept inside the moment's clean stretch; None if under 15 s fit."""
    lo, hi = m["span"][0] + GUARD_S, m["span"][1] - GUARD_S
    start, end = t - ACTION_BEFORE, t + ACTION_AFTER
    while end < hi and end - start < ACTION_LEN[1] and rec["act"][min(rec["n"] - 1, int(end / STEP))] >= 1.0:
        end += STEP
    if start < lo:
        end, start = min(hi, end + lo - start), lo
    if end > hi:
        start, end = max(lo, start - (end - hi)), hi
    end = min(end, start + ACTION_LEN[1])
    return (round(start, 2), round(end, 2)) if end - start >= ACTION_LEN[0] - 1e-6 else None


def narrated_window(rec, m):
    """A NARRATED Short's footage: from NARRATED_LEAD_S before the moment's own window (with LOOP_LEAD_S of clean
    footage before it for the loop ending), as long as the clean stretch allows, up to NARRATED_MAX_S, so everything
    the moment was scored on is in it."""
    lo, hi = m["span"][0] + GUARD_S + LOOP_LEAD_S, m["span"][1] - GUARD_S
    start = max(lo, m["start_s"] - NARRATED_LEAD_S)
    start = min(start, max(lo, hi - NARRATED_MAX_S))
    end = min(hi, start + NARRATED_MAX_S)
    return (round(start, 2), round(end, 2)) if end - start >= NARRATED_MIN_S - 1e-6 else None


def type_options(rec, m):
    """Both types for one moment: their windows, evidence, whether each fits, and how well."""
    t = anchor(rec, m)
    act = action_window(rec, m, t)
    nar = narrated_window(rec, m)
    out = {"anchor_s": round(t, 2)}
    terms = m["terms"]
    if act:
        items = evidence(rec, *act)
        strong = any(terms.get(k) for k in ("crash", "explosion", "stunt")) or terms.get("activity_label") == "shootout"
        out["action"] = {"window": list(act), "evidence": items,
                         "fits": bool(strong or terms.get("action", 0) >= ACTION_STRONG),
                         "fit": round(terms.get("action", 0) + sum(terms.get(k, 0) for k in ("crash", "explosion",
                                                                                            "stunt")), 2)}
    if nar:
        items = evidence(rec, *nar)
        n = distinct_items(items)
        out["narrated"] = {"window": list(nar), "evidence": items, "fits": n >= NARRATED_ITEMS, "items": n,
                           "fit": round(0.75 * n + 0.5 * terms.get("star_rise", 0), 2)}
    return out


def choose_types(picks, recs):
    """The type of each pick, with the reason. One that fits only one type gets it; one that fits both gets the
    better fit; one that fits neither gets whatever window it has (ACTION first) and a "weak moment" flag. Then, when
    all picks have one type and a pick could be the other, the pick with the smallest difference changes."""
    for m in picks:
        opts = type_options(recs[m["video"]], m)
        m["anchor_s"] = opts["anchor_s"]
        m["options"] = {k: v for k, v in opts.items() if k in ("action", "narrated")}
        fits = [k for k in ("action", "narrated") if opts.get(k, {}).get("fits")]
        if len(fits) == 1:
            m["type"], m["type_why"] = fits[0], f"only {fits[0]} fits"
        elif fits:
            k = max(fits, key=lambda k: (opts[k]["fit"], k == "action"))
            m["type"], m["type_why"] = k, (f"both fit; {k} fits better ({opts['action']['fit']:g} action vs "
                                           f"{opts['narrated']['fit']:g} narrated)")
        else:
            k = "action" if "action" in opts else "narrated" if "narrated" in opts else None
            m["type"], m["type_why"] = k, "neither type fits well: weak moment"
            m.setdefault("flags", []).append("WEAK MOMENT: no strong event and few things to describe")
    kinds = {m["type"] for m in picks if m["type"]}
    if len(kinds) == 1 and len(picks) > 1:
        other = "narrated" if "action" in kinds else "action"
        can = [m for m in picks if m["options"].get(other, {}).get("fits")]
        if can:
            m = min(can, key=lambda m: m["options"][m["type"]]["fit"] - m["options"][other]["fit"]
                    if m["type"] in m["options"] else 0)
            m["type_why"] = f"changed to {other} so the Shorts have both types (it fits {other} too)"
            m["type"] = other
    for m in picks:
        chosen = m["options"].get(m["type"]) if m["type"] else None
        if chosen:
            m["window"], m["evidence"] = chosen["window"], chosen["evidence"]
        else:
            m["window"], m["evidence"] = [m["start_s"], m["end_s"]], []
    return [m for m in picks if m["type"]]


# --- 7. Frames to look at ---

def moment_sheet(path, start, end, out, every=2.0, cols=4, width=400):
    """A JPEG of the footage start..end, one frame every `every` seconds in reading order, each with its time in the
    recording written on it. False if ffmpeg failed."""
    rows = max(1, math.ceil((end - start) / every / cols))
    font = "C\\:/Windows/Fonts/arial.ttf"
    graph = (f"fps=1/{every:g},scale={width}:-2,"
             f"drawtext=fontfile='{font}':text='%{{pts\\:hms}}':x=6:y=6:fontsize=22:fontcolor=white:"
             f"box=1:boxcolor=black@0.65:boxborderw=4,tile={cols}x{rows}")
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-copyts",
                           "-i", str(path), "-vf", graph, "-frames:v", "1", "-q:v", "4", str(out)],
                          capture_output=True, text=True)
    return done.returncode == 0 and Path(out).is_file()


# --- The whole prepare stage ---

def preflight(model, log=print):
    """Ollama up with the model, ffmpeg there, enough memory; LaunchError otherwise."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise LaunchError("ffmpeg and ffprobe must be on PATH")
    try:
        info = vlm.check_ollama(model)
    except vlm.VlmError as e:
        raise LaunchError(f"{e}. Open the Ollama app, then type /launch again.") from None
    others = [m for m in info["loaded"] if m != model]
    if others:
        log(f"  Note: Ollama has {', '.join(others)} loaded; they are unloaded first so the model fits the GPU.")
        for m in others:
            vlm.unload(m)
    free = index.free_ram_gb()
    if free is not None and free < index.RELOAD_BELOW_GB:
        raise LaunchError(f"only {free:.1f} GB of memory is free; close other programs (a browser with many tabs, "
                          "ComfyUI) and type /launch again")
    return free


def prepare(game="gta6", profile=None, videos=None, every=index.EVERY_S, dry_run=False, input_dir=INPUT,
            shorts_dir=SHORTS_DIR, library_dir=index.LIBRARY, day=None, slots=None, safety=None, log=print,
            index_video=None, guided_run=None, clip_runner=None, check=True, sheets=True):
    """The prepare stage; returns the run folder (with candidates.json). index_video, guided_run, clip_runner and
    safety are swapped for stand-ins by the tests; check=False skips the Ollama/memory pre-flight."""
    t_start = time.perf_counter()
    timing = {}
    if game not in GAMES:
        raise LaunchError(f"--game is one of {', '.join(GAMES)}")
    profile = profile or game
    day = day or date.today().isoformat()
    runs = {"runs": []} if dry_run else load_runs(shorts_dir)
    if check:
        preflight(index.MODEL, log)
    recordings, skipped = find_recordings(input_dir, library_dir, runs,
                                          videos or ([str(v) for v in sorted(Path(input_dir).iterdir())
                                                      if v.suffix.lower() in index.VIDEO_EXTS] if dry_run else None))
    for name, why in skipped.items():
        log(f"  Skipped {name}: {why}")
    if not recordings:
        raise LaunchError(f"no new recordings in {input_dir}. Copy the PS5 files there first (every file already "
                          "in the library or taken by an earlier /launch run is skipped)")
    folder = run_folder(shorts_dir, day, runs, dry_run)
    folder.mkdir(parents=True, exist_ok=True)
    log(f"Run folder: {folder}")
    lib_dir, cache_dir = Path(library_dir), None
    if dry_run:
        # Everything the trial makes stays in its folder: profiles, calibration sessions, the library. The model's
        # answers come from (and go to) the main cache: they don't depend on the profile.
        lib_dir, cache_dir = folder / "library", Path(library_dir) / "_cache"
        prof_dir = folder / "profiles"
        prof_dir.mkdir(exist_ok=True)
        for f in (HERE / "profiles").glob("*_nohud.json"):
            shutil.copyfile(f, prof_dir / f.name)
        clip.PROFILE_DIR, cal.CALIB_ROOT = prof_dir, folder / "calib"
        os.environ["GTA_CLIPPER_PROFILES"] = str(prof_dir)    # for clip.py and star_check.py, run as subprocesses
    note_run(shorts_dir, folder, recordings, "preparing", dry_run)
    durations = {v.stem: probe_duration(v) for v in recordings}
    log(f"Recordings ({len(recordings)}, {sum(durations.values()) / 60:.1f} min): "
        + ", ".join(f"{v.name} ({hms(durations[v.stem])})" for v in recordings))

    # 2. The profile.
    t = time.perf_counter()
    choice = choose_profile(profile, game, recordings, durations, slots or GAMES[game]["slots"], log, guided_run)
    timing["calibration"] = round(time.perf_counter() - t, 1)
    log(f"Profile: {choice['profile']} ({'HUD rules on' if choice['mode'] == 'hud' else 'NO-HUD MODE'}): "
        f"{choice['why']}")

    # 3. Index, with clip.py running alongside.
    t = time.perf_counter()
    runner = clip_runner or (lambda v, p: run_clip(v, p, folder / "clip_runs", folder / "logs"))
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    clip_jobs = {v.stem: pool.submit(runner, v, choice["profile"]) for v in recordings}
    do_index = index_video or index.index_video
    indexes, modes, checks = {}, {}, {}
    try:
        for v in recordings:
            log(f"Indexing {v.name} with {choice['profile']}...")
            idx = do_index(v, profile=choice["profile"], every=every, library=lib_dir, cache_dir=cache_dir,
                           log=lambda m: log("  " + m))
            idx["_dir"] = str(lib_dir / v.stem)
            mode = choice["mode"]
            if mode == "hud":
                try:
                    tl = library.timeline_of(idx)
                    ok, reasons, numbers = hud_check(idx, tl)
                except library.LibraryError as e:
                    ok, reasons, numbers = False, [str(e)], {}
                checks[v.stem] = {"ok": ok, "reasons": reasons, "numbers": numbers}
                if not ok:
                    log(f"  The HUD reading of {v.name} can't be trusted ({'; '.join(reasons)}): NO-HUD MODE for it.")
                    mode = "nohud"
                    idx = do_index(v, profile=f"{game}_nohud", every=every, library=lib_dir, cache_dir=cache_dir,
                                   log=lambda m: log("  " + m))
                    idx["_dir"] = str(lib_dir / v.stem)
            indexes[v.stem], modes[v.stem] = idx, mode
        timing["index"] = round(time.perf_counter() - t, 1)
        t = time.perf_counter()
        clip_runs = {stem: job.result() for stem, job in clip_jobs.items()}
        timing["clip_wait_after_index"] = round(time.perf_counter() - t, 1)
    finally:
        pool.shutdown(wait=True)
    for stem, run in clip_runs.items():
        if run is None:
            log(f"  WARNING: clip.py failed on {stem} (see {folder / 'logs'}); its action score counts as 0.")

    # 5. Moments.
    t = time.perf_counter()
    used = [] if dry_run else [tuple(s) for s in all_used_spans(shorts_dir)]
    recs, offset = {}, 0.0
    for v in recordings:
        idx = indexes[v.stem]
        act = action_series(clip_runs.get(v.stem), math.ceil(idx["video"]["duration_s"] / STEP))
        recs[v.stem] = recording_data(v.stem, idx, act, modes[v.stem], offset)
        offset += idx["video"]["duration_s"]
    moments = [m for rec in recs.values() for m in mine(rec, used)]
    moments.sort(key=lambda m: -m["score"])
    for k, m in enumerate(moments, 1):
        m["id"] = f"M{k}"
    log(f"Moments: {len(moments)} found; the best: "
        + "; ".join(f"{m['id']} {m['video']} {hms(m['start_s'])} {m['category']} {m['score']:.1f}" for m in moments[:5]))
    ok = (lambda m: safety(m, recs[m["video"]], folder)) if safety else (lambda m: (True, []))
    picks, rejected = pick_three(moments, offset, ok, log=log)
    picks = choose_types(picks, recs)
    timing["mine_and_safety"] = round(time.perf_counter() - t, 1)
    if not picks:
        raise LaunchError("no moment passed the checks: nothing to make a Short from")
    if sheets:
        (folder / "moments").mkdir(exist_ok=True)
        for m in picks:
            out = folder / "moments" / f"{m['id']}.jpg"
            m["sheet"] = str(out) if moment_sheet(m["path"], m["window"][0], m["window"][1], out) else None
    for n, m in enumerate(picks, 1):
        m["short"] = n
        if m["mode"] == "nohud":
            m.setdefault("flags", []).append("NO-HUD MODE: no wanted-star counts; narration may not mention stars")
    timing["total"] = round(time.perf_counter() - t_start, 1)
    data = {"version": 1, "created": datetime.now().isoformat(timespec="seconds"), "dry_run": dry_run,
            "game": game, "game_name": GAMES[game]["name"], "folder": str(folder),
            "recordings": [{"name": v.name, "stem": v.stem, "path": str(v), "duration_s": round(durations[v.stem], 2),
                            "session_offset_s": round(recs[v.stem]["offset_s"], 2), "mode": modes[v.stem],
                            "profile": indexes[v.stem]["profile"]["name"], "hud_check": checks.get(v.stem),
                            "clip_run": str(clip_runs.get(v.stem)) if clip_runs.get(v.stem) else None}
                           for v in recordings],
            "session_s": round(offset, 2), "skipped": skipped, "calibration": choice,
            "settings": {"every_s": every, "window_s": WINDOW_S, "library": str(lib_dir)},
            "moments_found": len(moments),
            "top_moments": [{k: m[k] for k in ("id", "video", "start_s", "end_s", "session_s", "score", "category",
                                               "terms")} for m in moments[:10]],
            "rejected": rejected,
            "picks": [{k: v for k, v in m.items() if k not in ("options",)} | {
                "other_type": {k: {"window": o["window"], "fits": o["fits"], "fit": o["fit"]}
                               for k, o in m["options"].items() if k != m["type"]}} for m in picks],
            "timing_s": timing}
    write_json(folder / "candidates.json", data)
    note_run(shorts_dir, folder, recordings, "prepared", dry_run)
    log(f"Picked {len(picks)}: " + "; ".join(f"Short {m['short']}: {m['type'].upper()} {m['video']} "
                                              f"{hms(m['window'][0])}-{hms(m['window'][1])} ({m['category']})"
                                              for m in picks))
    log(f"Wrote {folder / 'candidates.json'} in {timing['total'] / 60:.1f} min")
    return folder


def all_used_spans(shorts_dir):
    """Every (video, start, end) any earlier Short showed (all of them, not only the last few: /launch never shows
    footage twice)."""
    return [tuple(span) for s in footage_short.load_used(shorts_dir)["shorts"] for span in s.get("spans", [])]


def main():
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="/launch: Shorts from your own gameplay, start to finish.")
    sub = ap.add_subparsers(dest="stage", required=True)
    p = sub.add_parser("prepare", help="find, calibrate, index, mine and pick: writes candidates.json")
    p.add_argument("--game", default="gta6", choices=tuple(GAMES))
    p.add_argument("--profile", help="HUD profile name (default: the game's, e.g. gta6)")
    p.add_argument("--videos", nargs="+", help="these recordings instead of the new ones in the input folder")
    p.add_argument("--input", type=Path, default=INPUT, help=f"where the recordings are (default {INPUT})")
    p.add_argument("--every", type=float, default=index.EVERY_S,
                   help=f"seconds of video per index sheet (default {index.EVERY_S:g})")
    p.add_argument("--star-slots", type=int, help="wanted-star slots for calibration (default: the game's)")
    p.add_argument("--dry-run", action="store_true",
                   help="a trial: its own folder, profile and library; _used_footage.json is neither read nor written")
    p.add_argument("--date", help="the date in the folder name (default today)")
    args = ap.parse_args()
    try:
        if args.stage == "prepare":
            prepare(args.game, args.profile, args.videos, args.every, args.dry_run, args.input, day=args.date,
                    slots=args.star_slots, log=lambda m: print(m, flush=True))
    except (LaunchError, index.IndexError_) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
