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
  5. brings the content bank up to date (bank.py, PR 32): every moment of every recording, labelled from its peak. Only
     moments that are new, or rendered and not posted, may be picked. (A dry run, or --videos, mines the recordings
     themselves instead: mine, every 30 s window of clean footage with points for action, star rises, wanted stars,
     what the player is doing, crashes, explosions and stunts the model saw, police and the model's confidence.)
  6. picks 3 (pick_varied): the clearest payoff first, then points; a moment that fails the safety checks or shows
     posted or rejected footage never counts, a category already picked costs a point.
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
import cutplan
import fastff
import footage_short
import heavy
import index
import library
import safety as safety_checks
import truth
import vlm

HERE = Path(__file__).resolve().parent
INPUT = Path(r"C:\AI\gta\input")
SHORTS_DIR = footage_short.SHORTS_DIR
RUNS_LOG = "_launch_runs.json"           # in the shorts folder: every /launch run and the recordings it took
GAMES = {"gta6": {"name": "GTA 6", "slots": 6, "hashtag": "#GTA6"},
         "gta5": {"name": "GTA V", "slots": 5, "hashtag": "#GTA5"}}
GAME_FILES = {"gta6": "Grand Theft Auto VI_"}    # how the PS5 starts a recording's name: what isn't called that is not of
                                                 # the game (the input folder also holds GTA 5 recordings)
STEP = clip.BUCKET                       # 0.5 s: the HUD and the action score are per half second
GUARD_S = footage_short.GUARD_S          # footage keeps this far from a never_use part
LOOP_LEAD_S = 2.0                        # a narrated Short's loop ending replays this much from before its start

# --- Is the HUD reading trustworthy? (hud_check) ---
GAMEPLAY = ("driving", "on_foot", "shootout", "chase", "flying", "boat", "swimming")
NO_HUD_LABELS = ("cutscene", "menu_map", "loading")
# Of the sheets the model calls gameplay, at least this share must show the minimap. It was 0.8 until the first dry
# run (Nov 17): the hand-made gta5.json itself scored 0.72-0.89 on the 4 dev recordings, because the model calls
# about a quarter of the cutscenes in story-heavy footage gameplay. 0.5 still catches a profile that misses the
# minimap half the time, with room for a cutscene-heavy first hour.
AGREE_MIN = 0.5
AGREE_MIN_SHEETS = 10    # ...checked once there are this many
HIDDEN_MIN = 0.5         # of the sheets it calls a cutscene, menu or loading, at least this share must show none
HIDDEN_MIN_SHEETS = 5
FLICKER_PER_MIN = 6.0    # more wanted-star changes a minute than this is a misread, not a wanted level
SURE = 0.6               # a model answer with lower confidence is not used in the check

# --- Moments (mine) ---
WINDOW_S = 30.0          # moments are scored as windows of this length (shorter where clean footage is shorter)
MIN_WINDOW_S = 15.0
MIN_RECORDING_S = 15.0   # launch-fixes: a recording under this is skipped (it can't hold a Short)
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
DIALOGUE_COST = 1.5      # launch-fixes: a moment with mission dialogue on most of it is worth this much less (x share)
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
    return index.read_record(Path(shorts_dir) / RUNS_LOG, "runs") or {"runs": []}


def find_recordings(input_dir, library_dir, runs, explicit=None, game=None):
    """(the recordings to use, in session order, {name: why it was skipped}). explicit: the --videos list, used as
    given. Otherwise every video in input_dir that is not in the library yet and not part of an earlier run; with a
    game of GAME_FILES, only that game's (PR 35: a GTA 5 file in the folder was taken for GTA 6). A run still
    "preparing" (it stopped) keeps its own recordings: they are taken again."""
    skipped = {}
    if explicit:
        videos = [Path(v) for v in explicit]
        missing = [str(v) for v in videos if not v.is_file()]
        if missing:
            raise LaunchError("not found: " + ", ".join(missing))
    else:
        # (a run that only read the content bank has no recordings to carry on with)
        unfinished = [r for r in runs["runs"] if r.get("status") == "preparing" and r.get("recordings")]
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
        if not explicit and not v.name.startswith(GAME_FILES.get(game, "")):
            skipped[v.name] = f"not a {GAMES[game]['name']} recording (its name doesn't start with {GAME_FILES[game]})"
        elif barred:
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


def guided_failure(name):
    """Why the last guided calibration of this profile saved nothing or failed, in its own words: the reason the
    unattended page gave up, else the problems its check found."""
    session = read_json(cal.CALIB_ROOT / name / "session.json") or {}
    failed = [e for e in session.get("log", []) if e.get("event") == "auto_accept_failed"]
    if failed:
        return failed[-1].get("why")
    result = read_json(cal.CALIB_ROOT / name / "guided_result.json") or {}
    return "; ".join(result.get("problems") or []) or None


def choose_profile(name, game, recordings, durations, slots, log=print, guided_run=None, tries=3):
    """The profile to index with: {"profile", "mode": "hud" or "nohud", "stars": True/False, "why", "attempts"}.

    - profiles/<name>.json exists, its stars are done, and its last guided check (if any) didn't FAIL: use it.
    - else guided calibration with every guess accepted, on up to `tries` recordings at once (longest first): the
      calibration scans each of them and uses the one that shows the HUD best (launch-fixes: launch night tried one
      recording and gave up). "PASS" ends it; "PASS for the minimap. STARS NOT CALIBRATED" tries the next recordings
      for the stars, and uses the profile without stars if none shows any; FAIL, or a run that can't start or save,
      means NO-HUD, with the calibration's own reason.
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
    order = sorted(recordings, key=lambda v: -durations.get(Path(v).stem, 0.0))
    for k in range(0, min(len(order), 3 * tries), tries):
        batch = [Path(v) for v in order[k:k + tries]]
        errors = []

        def glog(m, errors=errors):
            if m.startswith("ERROR:"):
                errors.append(m[6:].strip())
            log("    " + m)
        log(f"  Guided calibration of {name} on {', '.join(v.name for v in batch)}, every guess accepted...")
        code = guided_run(name, batch, slots=slots, game=GAMES.get(game, {}).get("name"), browser=False,
                          log=glog, serve=guided.auto_accept)
        result = last_guided_result(name) if code != 2 else None
        failed = code != 0 or not result or result.startswith("FAIL") or result.startswith("SAVED")
        why = ((errors[-1] if errors else guided_failure(name)) if failed else None)
        attempts.append({"videos": [v.name for v in batch], "exit": code, "result": result, "why": why})
        if failed:
            return {"profile": nohud, "mode": "nohud", "stars": False, "attempts": attempts,
                    "why": f"guided calibration on {', '.join(v.name for v in batch)} "
                           + (f"ended {result}" if result else "could not start or save a profile")
                           + (f": {why}" if why else "")}
        if "STARS NOT CALIBRATED" not in result:
            return {"profile": name, "mode": "hud", "stars": True, "attempts": attempts,
                    "why": f"guided calibration PASS on {', '.join(v.name for v in batch)}"}
    if guided.pending_parts(name) is not None:
        return {"profile": name, "mode": "hud", "stars": False, "attempts": attempts,
                "why": "guided calibration PASS for the minimap; no recording showed wanted stars, so star counts are "
                       "off and narration may not mention stars"}
    return {"profile": nohud, "mode": "nohud", "stars": False, "attempts": attempts, "why": "no recording to calibrate on"}


# --- 3. clip.py's action score ---

def clip_count(duration_s):
    return max(3, math.ceil(duration_s / 360.0))


CLIP_DONE = "launch_clip_done.json"      # in a clip.py run folder: written by run_clip when that run finished


def finished_clip_run(video, profile, out_root):
    """launch-fixes: a stopped prepare carries on where it stopped, so a clip.py run this run folder already finished
    for the same recording and profile is used again instead of running clip.py once more. None if there is none."""
    runs = sorted((Path(out_root) / Path(video).stem).glob("*/" + CLIP_DONE))         if (Path(out_root) / Path(video).stem).is_dir() else []
    for done in reversed(runs):
        data = read_json(done) or {}
        if data.get("profile") == profile and (done.parent / "scores.json").is_file():
            return done.parent
    return None


def run_clip(video, profile, out_root, logs_dir):
    """clip.py on one recording, as it is (a subprocess, so its own rules and output stay exactly as always). Returns
    its run folder, or None if it failed (the log says why). A run already finished in this folder is used again."""
    out_root, logs_dir = Path(out_root), Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    again = finished_clip_run(video, profile, out_root)
    if again:
        return again
    before = set((out_root / Path(video).stem).glob("*")) if (out_root / Path(video).stem).is_dir() else set()
    with open(logs_dir / f"clip_{Path(video).stem}.txt", "w", encoding="utf-8", errors="replace") as fh:
        done = subprocess.run([sys.executable, str(HERE / "clip.py"), str(video), "--profile", profile,
                               "--count", str(clip_count(probe_duration(video))), "--out", str(out_root)],
                              stdout=fh, stderr=subprocess.STDOUT, cwd=HERE)
    new = sorted(set((out_root / Path(video).stem).glob("*")) - before)
    if done.returncode == 0 and new:
        write_json(new[-1] / CLIP_DONE, {"profile": profile, "video": str(video),
                                         "finished": datetime.now().isoformat(timespec="seconds")})
        return new[-1]
    return None


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


def names_shooting(text):
    """Whether a line of the model's uses one of the words truth.py keeps for a shootout (shoot, gun, firing ...), read
    where it isn't denied. EVENTS has no shooting (an index segment is one), so this is what the words are checked by."""
    family = next(words for words, _ in truth.WORDS if "shootout" in words)
    return any(truth.has_word(text or "", w) for w in family)


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


def verify_terms(rec, m):
    """launch-fixes: a moment's crash, explosion, stunt, police and shootout points stand only where the safety answers
    (m["verified"]) saw them in its own frames, and its score and category follow; long mission dialogue (subtitles
    on most of its safety sheets) costs DIALOGUE_COST. The labels taken away are kept in m["dropped_labels"]."""
    v = m.get("verified")
    if v is None:                                # no safety answers (a test of the older steps)
        return []
    a, b = m["start_s"], m["end_s"]
    terms, dropped = dict(m["terms"]), []
    for k in ("crash", "explosion", "stunt"):
        if k in terms and not seen_near(v, VERIFY[k], a, b):
            dropped.append(k)
            del terms[k]
    if "police" in terms and not seen_near(v, "police", a, b) and not terms.get("stars"):
        dropped.append("police")
        del terms["police"]
    if terms.get("activity_label") == "shootout" and not seen_near(v, "shooting", a, b):
        dropped.append("shootout")
        terms.pop("activity", None)
        terms.pop("activity_label", None)
    share = mission_share(m, (a, b))
    if share >= MISSION_SHARE:
        terms["dialogue"] = round(-DIALOGUE_COST * share, 2)
    m["terms"], m["dropped_labels"] = terms, dropped
    m["score"] = round(sum(v for v in terms.values() if isinstance(v, (int, float))), 3)
    m["category"] = category_of(terms)
    return dropped


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


# --- 5b. Topics first (launch-fixes) ---
# The owner's rule: the main output is Shorts that teach one thing about GTA 6, with his footage as the proof. So the
# pool starts from topics: shotlist.json's (each shot a library query) and the GTA 6 HUD's own (heat icons, the look of
# the stars, read by proof.hud6). A footage match becomes a topic candidate; the safety and label checks then decide
# whether the frames really show it. The facts a topic may use come from facts.json (TOPIC_FACTS: ids only).
TOPIC_BONUS = 1.5            # a topic candidate is worth this much more than a moment of the same score
TOPIC_WINDOW_S = 34.0        # a topic candidate's window round its match
TOPIC_FACTS = {"wanted-system": ["wanted-six-stars", "wanted-witness-or-alarm"],
               "police-chase": ["wanted-six-stars"],
               "cops-outfit-car-change": ["wanted-police-know-outfit-car", "evidence-masks-clothes"],
               "big-chases": ["wanted-six-stars"],
               "heat-icons": ["wanted-heat-icons", "wanted-police-know-outfit-car", "evidence-masks-clothes"],
               "star-looks": ["wanted-hollow-star", "wanted-red-star", "wanted-solid-star", "wanted-six-stars"]}
HUD6_TOPICS = {"heat": ("heat-icons", "the small icons under the wanted stars (what the police know) change"),
               "star look": ("star-looks", "the wanted stars change how they look (solid, outline)")}
STAR_STRETCH_S = 10.0        # stars showing this long is worth reading with proof.hud6


def topic_window(rec, at_s, length=TOPIC_WINDOW_S):
    """A window of about `length` seconds round at_s inside one clean stretch (GUARD_S from its ends), or None."""
    for a, b in rec["spans"]:
        lo, hi = a + GUARD_S, b - GUARD_S
        if lo <= at_s <= hi and hi - lo >= MIN_WINDOW_S:
            start = min(max(lo, at_s - length * 0.4), max(lo, hi - length))
            return round(start, 2), round(min(hi, start + length), 2), (a, b)
    return None


def topic_moment(rec, topic, shot, why, at_s, used, extra=None):
    """One topic candidate (a moment with "topic") round at_s, scored like a mined moment plus TOPIC_BONUS."""
    found = topic_window(rec, at_s)
    if not found:
        return None
    a, b, span = found
    if any(v == rec["stem"] and a < e and s < b for v, s, e in used):
        return None
    points, terms = score_window(rec, a, b)
    terms["topic"] = TOPIC_BONUS
    m = {"video": rec["stem"], "path": rec["path"], "profile": rec["profile"], "mode": rec["mode"], "start_s": a,
         "end_s": b, "span": [round(span[0], 2), round(span[1], 2)], "session_s": round(rec["offset_s"] + (a + b) / 2, 1),
         "score": round(points + TOPIC_BONUS, 3), "terms": terms, "category": f"topic: {topic}",
         "topic": {"id": topic, "shot": shot, "why": why, "at_s": round(at_s, 2), "facts": TOPIC_FACTS.get(topic, [])}}
    if extra:
        m["topic"].update(extra)
    return m


def topic_candidates(recs, used=(), shotlist=None, hud6=None, log=print):
    """launch-fixes: the topic candidates of the session, best first: every shot of a shotlist.json topic with facts
    to teach (TOPIC_FACTS; the others are B-roll, not topics) that this night's footage matches (its library query
    over the session's indexes), and, on GTA 6 with its HUD read, every change
    of the heat icons or the stars' look (proof.hud6) while stars show. At most one candidate per topic and recording
    stretch."""
    import proof
    shotlist = shotlist if shotlist is not None else library.load_shotlist()
    indexes = [r["idx"] for r in recs.values()]
    out = []
    for topic in shotlist.get("topics", []):
        if topic.get("id") not in TOPIC_FACTS:       # a shot-list topic with no facts to teach is B-roll, not a topic
            continue
        for shot in topic.get("shots", []):
            try:
                rows = library.find(indexes, **shot["query"])
            except library.LibraryError:
                continue
            for row in rows:
                rec = recs.get(row["video"])
                if rec is None:
                    continue
                hits = row.get("text_hits") or []
                at = hits[0]["t"] if hits else (row["start_s"] + row["end_s"]) / 2
                m = topic_moment(rec, topic["id"], shot["id"], shot.get("want", ""), at, used)
                if m:
                    out.append(m)
    hud6 = hud6 or proof.hud6
    for rec in recs.values():
        if rec["mode"] != "hud":
            continue
        try:
            prof = clip.load_profile(rec["profile"])
        except clip.ProfileError:
            continue
        if getattr(prof, "star_method", "outline") != "gta6":
            continue
        for a, b in clip.runs([c > 0 for c in rec["count"]]):
            if b - a < STAR_STRETCH_S:
                continue
            series = hud6(rec["path"], a, b, prof)
            for e in proof.hud6_events(series):
                topic, why = HUD6_TOPICS[e["kind"]]
                m = topic_moment(rec, topic, e["kind"], f"{why}: {e['before']} to {e['after']} at {hms(e['at_s'])}",
                                 e["at_s"], used, {"event": e})
                if m:
                    m["terms"]["hud6"] = 1.0
                    m["score"] = round(m["score"] + 1.0, 3)
                    out.append(m)
    best = {}
    for m in sorted(out, key=lambda m: -m["score"]):
        key = (m["topic"]["id"], m["video"], m["span"][0])
        if key not in best:
            best[key] = m
    picked = sorted(best.values(), key=lambda m: -m["score"])
    log(f"Topics: {len(picked)} topic candidate(s) from the shot list and the HUD"
        + (": " + "; ".join(f"{m['topic']['id']} ({m['video'][-6:]} {hms(m['start_s'])})" for m in picked[:8])
           if picked else ""))
    return picked


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
                    log(f"  {m['video']} {hms(m['start_s'])}-{hms(m['end_s'])} was skipped (safety checks or the quality gate): "
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


POOL_MIN, POOL_MAX = 15, 25   # launch-fixes: the candidates checked before picking (5 per Short asked for, in this range)
POOL_APART_S = 60.0           # two candidates of one recording keep this far apart (start to start)


def pool_of(moments, size, apart=POOL_APART_S, rank=None):
    """launch-fixes: the candidates worth checking: the best moments, spread over the recordings. Best first (by
    rank(m), the score by default; PR 29: pool_rank), a moment joins when it is POOL_APART_S from the others of its
    recording and its recording has fewer than its share (the pool size over the number of recordings, plus one)."""
    rank = rank or (lambda m: m["score"])
    recordings = {m["video"] for m in moments}
    cap = max(2, -(-size // max(1, len(recordings))) + 1)
    pool, count = [], {}
    for m in sorted(moments, key=lambda m: (-rank(m), m["session_s"])):
        near = any(p["video"] == m["video"] and abs(p["start_s"] - m["start_s"]) < apart for p in pool)
        if not near and count.get(m["video"], 0) < cap:
            pool.append(m)
            count[m["video"]] = count.get(m["video"], 0) + 1
        if len(pool) >= size:
            break
    return pool


def pick_pool(moments, ok, count=3, size=None, log=print, posted=(), text_of=None):
    """launch-fixes: (picks, rejected, pool). Every candidate of the pool (pool_of) goes through ok() (the safety checks,
    the labels checked against the frames, the quality gate), not only the few that would be picked; then the `count`
    picks are made by pick_varied (PR 29: payoff strength first, no near-duplicates, at most PER_RECORDING a recording,
    both types, posted topics cost). rejected lists every candidate that failed, with why. posted: the posted-topics
    list (load_posted); text_of(m): what the frames say about m (moment_text), for matching it."""
    size = size or max(POOL_MIN, min(POOL_MAX, 5 * count))
    pool = pool_of(moments, size, rank=lambda m: pool_rank(m, posted, text_of))
    passed, rejected = [], []
    for k, m in enumerate(pool, 1):
        good, why = ok(m)
        m["checked"] = {"passed": bool(good), "why": list(why)}
        if good:
            passed.append(m)
        else:
            rejected.append({"id": m.get("id"), "video": m["video"], "start_s": m["start_s"], "end_s": m["end_s"],
                             "score": m["score"], "category": m.get("category"), "why": why})
            log(f"  {m.get('id')} {m['video']} {hms(m['start_s'])}-{hms(m['end_s'])} failed: " + "; ".join(why))
        if k % 5 == 0:
            log(f"  checked {k} of {len(pool)} candidates: {len(passed)} pass so far")
    picked = pick_varied(passed, count, posted, text_of, log)
    for m in passed:
        if m not in picked:
            m["checked"]["not_picked"] = m.get("not_picked_why") or "passed, but others scored higher"
    return picked, rejected, pool


# --- 5c. Variety (PR 29): payoff strength first, no near-duplicates, both types, posted topics ---
# The Nov 20 run picked three "stars appear" moments of one recording (M1, M5 and M8 of _135610: only one was usable),
# while the night's best moment, the motorbike crash (M25), came in only on a --count 6 re-run. So the picks are now
# ranked by how clear their payoff is before anything else, two picks of one recording must be clearly different
# moments, a run aims for both an action clip and a topic, and a moment that repeats a topic already posted costs points.
IMPACT_TERMS = ("crash", "explosion", "stunt")
PAYOFF_LABELS = {3: "impact", 2: "HUD change with a visible cause", 1: "no single clear event", 0: "quiet"}
PER_RECORDING = 2            # picks from one recording, unless nothing else qualifies
NEAR_DUP_S = 600.0           # two picks of one recording of the same kind closer than this are the same moment twice
                             # (M1 and M5 of the Nov 20 run were 321 s apart and the owner called them near-identical)
REPEAT_COST, STRONG_REPEAT_COST = 2.0, 4.0   # a moment repeating a posted topic (a "strong" one: the owner's worst)
POOL_PAYOFF_BONUS = 1.0      # pool_of: a candidate's place in the pool also counts its payoff tier this much a step
POSTED_FILE = HERE / "posted_topics.json"    # the topics already posted (the owner's list)
RULE_KEYS = {"category", "topic", "term", "profile", "words", "all_words"}
RECENT_POSTED = 2            # the owner (Nov 21): no crash while a crash is one of the last 2 posted Shorts, unless
                             # no other type is left that meets the payoff bar; then one such pick, not two.
PAYOFF_BAR = 2               # the owner (Nov 22): no pick, of any kind, has a payoff below 2/3 (an impact, or a HUD
                             # change with a visible cause). Fewer Shorts rather than a weak one.
FAILED_SHARE = 0.5           # a moment this much inside a Short that failed its shot check at render is left out


def payoff_strength(m):
    """(tier 0-3, label, why): how clear and visible a moment's payoff is. 3: an impact (a crash, explosion or stunt
    its own frames confirmed: verify_terms drops one they don't show); 2: a HUD change with a visible cause (the stars
    rise with police or a shootout on screen); 1: a HUD change alone, or action with no single event; 0: quiet."""
    terms = m.get("terms") or {}
    point = (m.get("point") or {}).get("action_payoff") or []
    hits = [k for k in IMPACT_TERMS if terms.get(k) or k in point]
    if hits:
        return 3, PAYOFF_LABELS[3], f"a {hits[0]} seen in its own frames"
    hud = bool(terms.get("star_rise") or terms.get("hud6") or "star rise" in point)
    cause = (["police"] if terms.get("police") else []) + (["a shootout"] if terms.get("activity_label") == "shootout"
                                                          else [])
    if hud and cause:
        return 2, PAYOFF_LABELS[2], f"the stars rise with {cause[0]} on screen"
    if hud:
        return 1, PAYOFF_LABELS[1], "the HUD changes (stars or icons), but no cause is seen"
    if terms.get("activity", 0) > 0:
        return 1, PAYOFF_LABELS[1], f"{terms.get('activity_label', 'action')} with no single clear event"
    return 0, PAYOFF_LABELS[0], "nothing clear happens"


def load_posted(path=POSTED_FILE, as_of=None, log=print):
    """The posted-topics list (posted_topics.json), read only. as_of: an ISO time; topics added after it are left out,
    and a "posted" day after its day counts as not posted yet (a re-pick of an old run sees the list as it was then; a
    topic with no "added" was on the owner's first list). A missing file means no topic costs, said in the log."""
    path = Path(path)
    data = index.read_record(path, "topics")
    if data is None:
        log(f"  No posted-topics list ({path}): no topic repeats are counted")
        return []
    out = []
    for t in data["topics"]:
        if not isinstance(t, dict) or not t.get("name") or not isinstance(t.get("rules"), list):
            raise LaunchError(f"{path}: every topic has a \"name\" and \"rules\" (a list): {t!r}")
        for r in t["rules"]:
            if not isinstance(r, dict) or not r or set(r) - RULE_KEYS:
                raise LaunchError(f"{path}: topic {t['name']!r}: a rule uses only {', '.join(sorted(RULE_KEYS))}")
        for k in ("posted", "planned"):
            if t.get(k) is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(t[k])):
                raise LaunchError(f"{path}: topic {t['name']!r}: {k} is a day, YYYY-MM-DD")
        if as_of and t.get("added") and str(t["added"]) > str(as_of):
            continue
        t = dict(t)
        if as_of and t.get("posted") and t["posted"] > str(as_of)[:10]:
            t.pop("posted")
        out.append(t)
    return out


def recent_posted(posted, n=RECENT_POSTED):
    """[(type, name)] of the last n POSTED topics (by their posted day, then their place in the list); a planned topic
    is not one of them until it has a posted day."""
    dated = [(t["posted"], k, t) for k, t in enumerate(posted) if t.get("posted")]
    return [(plain_type(t.get("type")), t["name"]) for _, _, t in sorted(dated, key=lambda x: x[:2])[-n:]]


def plain_type(label):
    """A type in the posted list's words: an impact named anywhere in the label ("truck crash", "car crashes",
    "big explosion", "topic: crash") is that impact, so labels written by hand and by the miner compare alike."""
    low = str(label or "").lower().replace("topic: ", "").strip()
    return next((k for k in IMPACT_TERMS if re.search(rf"(?<![a-z]){k}", low)), low) or None


def moment_type(m):
    """The kind of Short a moment makes, in the posted list's words: its impact (crash, explosion, stunt), from its
    verified terms, its action payoff or its own label, or else its category (star rise, chase, heat-icons ...)."""
    terms, point = m.get("terms") or {}, (m.get("point") or {}).get("action_payoff") or []
    hits = [k for k in IMPACT_TERMS if terms.get(k) or any(plain_type(x) == k for x in point)]
    return hits[0] if hits else plain_type(m.get("category", ""))


def unverified_in(m, s, every):
    """The events one sheet's line names (VERIFY: a crash, explosion, stunt, police, shooting) that nothing confirms for
    this moment (PR 35): the safety answers didn't see them in its frames; or, for a bank moment nobody has put through
    them yet, they are impacts or shooting its own label isn't sure of. [] where there is neither (older steps)."""
    named = {k for k in [*event_kinds(s.get("notable")), *(["shootout"] if names_shooting(s.get("notable")) else [])]
             if k in VERIFY}
    if m.get("verified") is not None:
        return sorted(k for k in named if not seen_near(m["verified"], VERIFY[k], s["t"], s["t"] + every))
    label = m.get("label")
    if label is None:
        return []
    sure = {"fall": "stunt", "shooting": "shootout"}.get(label.get("kind"), label.get("kind")) if label.get("sure") else None
    return sorted(k for k in named if k != "police" and k != sure)


def moment_text(rec, m):
    """What the frames say about a moment: the vision model's notable lines over its seconds, and a topic's reason. A line
    that names an event nothing confirms for this moment is left out (PR 35)."""
    a, b = m["start_s"], m["end_s"]
    lines = [s.get("notable") or "" for s in rec.get("sheets", []) if overlap(a, b, s["t"], s["t"] + rec["every"]) > 0
             and not unverified_in(m, s, rec["every"])]
    return " ".join(lines + [str((m.get("topic") or {}).get("why") or "")]).lower()


def rule_holds(rule, m, text):
    """Every condition of one posted-topic rule holds for the moment (each a list: any of it; all_words: all)."""
    terms = m.get("terms") or {}
    tests = {"category": lambda v: m.get("category") in v or str(m.get("category", "")).replace("topic: ", "") in v,
             "topic": lambda v: (m.get("topic") or {}).get("id") in v
             or str(m.get("category", "")).replace("topic: ", "") in v,
             "term": lambda v: any(terms.get(k) for k in v),
             "profile": lambda v: m.get("profile") in v,
             "words": lambda v: any(re.search(rf"\b{re.escape(w.lower())}", text) for w in v),
             "all_words": lambda v: all(re.search(rf"\b{re.escape(w.lower())}", text) for w in v)}
    return all(tests[k](v if isinstance(v, list) else [v]) for k, v in rule.items())


def topic_repeats(m, posted, text):
    """(cost, names): the posted topics this moment repeats and what that costs (the dearest one, not a sum)."""
    names, cost = [], 0.0
    for t in posted:
        if any(rule_holds(r, m, text) for r in t["rules"]):
            names.append(t["name"])
            cost = max(cost, STRONG_REPEAT_COST if t.get("strong") else REPEAT_COST)
    return cost, names


def pool_rank(m, posted=(), text_of=None):
    """A candidate's place in the pool: its score, its payoff tier (from its unchecked terms) and any posted-topic
    cost, so a strong crash isn't left out of the pool by a run of star rises (M25 only just made it on Nov 20)."""
    tier = payoff_strength(m)[0]
    cost = topic_repeats(m, posted, text_of(m) if text_of else "")[0] if posted else 0.0
    return m["score"] + POOL_PAYOFF_BONUS * tier - cost


def moment_name(m):
    return m.get("id") or f"{m['video'][-6:]} {hms(m['start_s'])}"


def near_duplicate(m, p):
    """Why m is the same moment again as the pick p, or None: same recording, same kind (category), and closer than
    NEAR_DUP_S. Two picks of one recording must differ in kind or be far apart."""
    if m["video"] != p["video"] or m.get("category") != p.get("category"):
        return None
    apart = abs(m["start_s"] - p["start_s"])
    if apart >= NEAR_DUP_S:
        return None
    return (f"the same kind of moment as {moment_name(p)} ({m.get('category')}, same recording, {apart:.0f} s apart): "
            "a near-duplicate")


def can_be(kind, m):
    """Can this moment make that kind of Short? action: an impact payoff; topic: something to teach in a narrated
    Short, i.e. a format the quality gate found (which needs a narrated window: the Nov 20 re-pick's M1 had a topic
    but only 17 s of clean footage, so it was made an action clip). Without the gate (older steps' tests), a topic."""
    if kind == "action":
        return payoff_strength(m)[0] == 3
    if m.get("point") is not None:
        return bool(m["point"].get("formats"))
    return bool(m.get("topic"))


def pick_varied(passed, count, posted=(), text_of=None, log=print):
    """The picks from the candidates that passed every check, best first (PR 29). A candidate whose payoff is below
    PAYOFF_BAR (2/3) is never picked: there may be fewer picks than `count`, or none. Each step takes the free candidate
    with the clearest payoff (payoff_strength's tier), then the most points: its score, less a posted topic it repeats
    (topic_repeats) and CATEGORY_COST for each pick already of its category. A candidate is not free when it is a
    near-duplicate of a pick or its recording has PER_RECORDING picks (that limit is lifted only when nothing else
    qualifies). Then, when the picks lack an action clip or a topic and a free candidate of that kind passed, it takes
    the place of the weakest pick that isn't the only one of the other kind. Every pick gets "payoff_strength" and
    "pick_why" (why it beat the others); every candidate left gets "not_picked_why"."""
    for m in passed:
        tier, label, why = payoff_strength(m)
        cost, names = topic_repeats(m, posted, text_of(m) if text_of else "")
        m["payoff_strength"] = {"tier": tier, "label": label, "why": why}
        m["repeats"], m["repeat_cost"] = names, cost
    # The payoff bar (the owner, Nov 22): a candidate below PAYOFF_BAR is never picked, by any route below (the
    # picks, the third pick of a recording, the action/topic swap), so a run may have fewer Shorts than asked.
    weak = [m for m in passed if m["payoff_strength"]["tier"] < PAYOFF_BAR]
    for m in weak:
        ps = m["payoff_strength"]
        m["not_picked_why"] = (f"payoff {ps['tier']}/3 ({ps['label']}: {ps['why']}) is below the minimum "
                               f"{PAYOFF_BAR}/3: a weak clip is not made")
    passed = [m for m in passed if m["payoff_strength"]["tier"] >= PAYOFF_BAR]
    recent = recent_posted(posted)
    recent_types = {t for t, _ in recent if t}

    def value(m, picked):
        return m["score"] - m["repeat_cost"] - CATEGORY_COST * sum(1 for p in picked if p["category"] == m["category"])

    def key(m, picked):
        # a moment a Short was already rendered from (and not posted) ranks after a new one of the same payoff
        return (m["payoff_strength"]["tier"], m.get("status") != "rendered", round(value(m, picked), 3), -m["session_s"])

    def held_back(m, picked, cap):
        for p in picked:
            why = near_duplicate(m, p)
            if why:
                return why
        same = sum(1 for p in picked if p["video"] == m["video"])
        return f"its recording already has {same} picks (at most {cap})" if same >= cap else None

    def bar_met_by_other(m, picked, cap):
        """The best other free candidate of a type not posted lately, or None. Every candidate left in `passed` met
        PAYOFF_BAR (the weak ones were taken out above), so any of them is good enough to take the place (the owner,
        Nov 21: M27's crash was picked right after the posted bike crash while M1 and M26 were there)."""
        return next((c for c in sorted(passed, key=lambda c: key(c, picked), reverse=True)
                     if c is not m and c not in picked and moment_type(c) not in recent_types
                     and not held_back(c, picked, cap)), None)

    def blocked(m, picked, cap):
        why = held_back(m, picked, cap)
        kind = moment_type(m)
        if why or kind not in recent_types:
            return why
        names = ", ".join(n for t, n in recent if t == kind)
        other = bar_met_by_other(m, picked, cap)
        if other is not None:
            return (f"a {kind} is one of the last {RECENT_POSTED} posted Shorts ({names}), and {moment_name(other)} "
                    f"(another type) meets the payoff bar ({PAYOFF_BAR}/3)")
        # No other type meets the bar: one pick of that type may come, not two (two crashes after a posted crash is
        # still crash after crash), even when nothing else is free: fewer Shorts rather than a repeat.
        if any(moment_type(p) == kind for p in picked):
            return f"this run already has a {kind}, right after a posted one ({names})"
        return None

    def describe(m, picked):
        ps = m["payoff_strength"]
        out = f"payoff {ps['tier']}/3 ({ps['label']}: {ps['why']}), {value(m, picked):.2f} points"
        if m["repeats"]:
            out += f" after -{m['repeat_cost']:g} for repeating posted topic(s): {', '.join(m['repeats'])}"
        return out

    picked, reasons = [], {}
    for cap in (PER_RECORDING, max(count, PER_RECORDING)):
        while len(picked) < count:
            free, held = [], []
            for m in passed:
                if m in picked:
                    continue
                why = blocked(m, picked, cap)
                (held if why else free).append((m, why))
            if not free:
                for m, w in held:               # nothing is left to pick: the last reasons are the final ones
                    reasons[id(m)] = w
                break
            ranked = sorted((m for m, _ in free), key=lambda m: key(m, picked), reverse=True)
            best = ranked[0]
            why = [describe(best, picked)]
            if ranked[1:]:
                r = ranked[1]
                rt, bt = r["payoff_strength"]["tier"], best["payoff_strength"]["tier"]
                why.append(f"beat {moment_name(r)} ({r['category']}): " + (
                    f"a weaker payoff ({rt}/3, {r['payoff_strength']['label']})" if rt < bt else
                    f"fewer points ({value(r, picked):.2f})"))
            above = [(m, w) for m, w in held if key(m, picked) > key(best, picked)]
            why += [f"{moment_name(m)} ranked higher but was left out: {w}" for m, w in above[:2]]
            if cap > PER_RECORDING:
                why.append(f"a third pick of its recording: nothing else qualified")
            if moment_type(best) in recent_types:
                why.append(f"a {moment_type(best)} like the last posted ("
                           + ", ".join(n for t, n in recent if t == moment_type(best))
                           + f"), picked because no other type is left that meets the payoff bar ({PAYOFF_BAR}/3)")
            best["pick_why"] = "; ".join(why)
            picked.append(best)
            for m, w in held:
                reasons[id(m)] = w
        if len(picked) >= count:
            break
    if count >= 2:
        for kind, other in (("action", "topic"), ("topic", "action")):
            if any(can_be(kind, p) for p in picked):
                continue
            for out in sorted(picked, key=lambda p: key(p, [q for q in picked if q is not p])):
                rest = [p for p in picked if p is not out]
                if can_be(other, out) and not any(can_be(other, p) for p in rest):
                    continue
                cands = [m for m in passed if m not in picked and can_be(kind, m) and not blocked(m, rest, PER_RECORDING)]
                if not cands:
                    continue
                best = max(cands, key=lambda m: key(m, rest))
                best["pick_why"] = (describe(best, rest) + f"; picked so the run has a{'n' if kind == 'action' else ''} "
                                    f"{kind} Short: it takes the place of {moment_name(out)}")
                reasons[id(out)] = f"its place went to {moment_name(best)}, so the run has a{'n' if kind == 'action' else ''} {kind} Short"
                out.pop("pick_why", None)
                picked = rest + [best]
                break
    for m in passed:
        if m not in picked:
            m["not_picked_why"] = reasons.get(id(m)) or (f"passed, but the picks ranked higher ({describe(m, picked)})")
    picked.sort(key=lambda m: key(m, []), reverse=True)
    for m in picked:
        log(f"  Picked {moment_name(m)} {m['video'][-6:]} {hms(m['start_s'])} ({m['category']}): {m['pick_why']}")
    if len(picked) < count:
        log(f"  Only {len(picked)} of {count} picks: the other candidates were near-duplicates, failed the checks "
            f"or had a payoff below {PAYOFF_BAR}/3 ({len(weak)} did)")
    return picked


# --- 6. Evidence and types ---

def sheet_picture(rec, t):
    idx_dir = Path(rec["idx"].get("_dir") or "")
    path = idx_dir / "frames" / index.frame_name(t)
    return str(path) if path.is_file() else None


# launch-fixes: what the safety answers must have seen for a label or an event to stay (safety.SEEN keys).
VERIFY = {"crash": "crash", "explosion": "explosion", "stunt": "stunt", "police": "police", "shootout": "shooting"}
VERIFY_SLACK_S = 2.0     # a seen answer this close to an item's own seconds counts for it


def seen_near(verified, what, a, b):
    """True when the safety answers saw `what` (a safety.SEEN key) within VERIFY_SLACK_S of a..b."""
    return any(f0 - VERIFY_SLACK_S < b and a < f1 + VERIFY_SLACK_S for f0, f1 in (verified or {}).get(what, []))


def evidence(rec, a, b, verified=None):
    """Everything the footage a..b shows that a line may point to, as items with an id (E1, E2 ...), a kind, the times
    in the recording, the numbers it supports and where it comes from. verified (launch-fixes): the safety answers'
    seen spans (safety.seen_spans); when given, a crash, explosion, stunt, police or shootout stays only where the model
    saw it in these frames, and a "seen" line keeps its words but marks the events it names that weren't seen
    ("not_seen"), so no narration can lean on them. Kinds:
      activity  an index segment's label (chase, shootout, driving ...), with its length in seconds
      stars     a wanted-star rise (HUD, the exact half second) or the stars showing (HUD count); never in NO-HUD mode
      police    police seen by the model
      event     crash, explosion, stunt or character, from a notable line
      seen      the model's notable line for one sheet (6 s), word for word"""
    items = []
    check = verified is not None
    for k, s in enumerate(rec["idx"]["segments"], 1):
        lo, hi = max(a, s["start_s"]), min(b, s["end_s"])
        if s["never_use"] or hi - lo < 1.0:
            continue
        value = s["activity"]
        if check and value == "shootout" and not seen_near(verified, "shooting", lo, hi):
            value = "on_foot"          # the index said shootout; nobody shoots in these frames
        items.append({"kind": "activity", "value": value, "from_s": round(lo, 2), "to_s": round(hi, 2),
                      "numbers": [round(hi - lo)], "source": f"index segment {k} ({s['decided_by']})"
                      + (" (no shooting seen: relabelled)" if value != s["activity"] else "")})
        if s["police_visible"] and (not check or seen_near(verified, "police", lo, hi)):
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
        t0, t1 = s["t"], s["t"] + rec["every"]
        named = list(s["kinds"]) + (["shootout"] if names_shooting(s["notable"]) else [])       # (PR 35: no kind of its own)
        unseen = [k for k in named if check and k in VERIFY and not seen_near(verified, VERIFY[k], t0, t1)]
        for k in s["kinds"]:
            if k in unseen:
                continue
            items.append({"kind": "police" if k == "police" else "event", "value": k if k != "police" else "police seen",
                          "from_s": s["t"], "to_s": round(s["t"] + rec["every"], 2), "numbers": [],
                          "source": f"vision model, sheet at {hms(s['t'])}: \"{s['notable']}\""
                                    + (" (seen again in the safety frames)" if check else ""), "picture": pic})
        seen_item = {"kind": "seen", "value": s["notable"], "from_s": s["t"], "to_s": round(s["t"] + rec["every"], 2),
                     "numbers": [], "source": f"vision model, sheet at {hms(s['t'])} (confidence "
                                              f"{s.get('confidence', 0):.2f})", "picture": pic}
        if unseen:
            seen_item["not_seen"] = unseen
        items.append(seen_item)
    items.sort(key=lambda e: (e["from_s"], e["kind"]))
    for k, e in enumerate(items, 1):
        e["id"] = f"E{k}"
    return items


STAR_LOOK_WORDS = {"red": "solid red", "red outline": "red outline", "white outline": "hollow white",
                   "white": "solid white"}


def hud6_evidence(rec, m, hud6=None):
    """launch-fixes: GTA 6's heat icons and star looks over a pick's window as evidence items (appended, new ids):
    each stretch of the same icon count or look (2 s or more), and each change. [] off GTA 6 or without its HUD."""
    import proof
    if rec["mode"] != "hud":
        return []
    try:
        prof = clip.load_profile(rec["profile"])
    except clip.ProfileError:
        return []
    if getattr(prof, "star_method", "outline") != "gta6":
        return []
    a, b = m["window"]
    series = (hud6 or proof.hud6)(rec["path"], a, b, prof)
    items = []
    for key, idx, word in (("heat", 3, "heat icon"), ("star_look", 2, "stars")):
        known = [(t, v) for t, *rest in series for v in [rest[idx - 1]] if v is not None]
        runs, start = [], None
        for k, (t, v) in enumerate(known):
            if start is None or v != known[k - 1][1] or t - known[k - 1][0] > 1.0:
                if start is not None:
                    runs.append(start)
                start = [t, t, v]
            start[1] = t + proof.STEP_S
        if start is not None:
            runs.append(start)
        for t0, t1, v in runs:
            if t1 - t0 < 2.0 or (key == "heat" and v == 0):
                continue
            value = (f"{v} {word}{'s' if v != 1 else ''} under the wanted stars" if key == "heat"
                     else f"{STAR_LOOK_WORDS.get(v, v)} wanted stars")
            items.append({"kind": key, "value": value, "from_s": round(t0, 2), "to_s": round(t1, 2),
                          "numbers": [v] if key == "heat" else [], "source": "GTA 6 HUD (proof.hud6, twice a second)"})
    for e in proof.hud6_events(series):
        if e["kind"] == "heat":
            items.append({"kind": "heat", "value": f"heat icons {e['before']} to {e['after']}", "from_s": e["at_s"],
                          "to_s": e["at_s"], "at_s": e["at_s"], "numbers": [e["before"], e["after"]],
                          "source": "GTA 6 HUD: the icons under the stars change"})
        else:
            items.append({"kind": "star_look", "value": f"the stars turn from {STAR_LOOK_WORDS.get(e['before'])} to "
                                                        f"{STAR_LOOK_WORDS.get(e['after'])}",
                          "from_s": e["at_s"], "to_s": e["at_s"], "at_s": e["at_s"], "numbers": [],
                          "source": "GTA 6 HUD: the stars change how they look"})
    n = len(m["evidence"])
    for k, e in enumerate(sorted(items, key=lambda e: e["from_s"]), n + 1):
        e["id"] = f"E{k}"
    return sorted(items, key=lambda e: e["from_s"])


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
    """The moment's main instant: a topic candidate's match (launch-fixes), else the peak the content bank found, else a
    star rise, else a crash/explosion/stunt sheet's middle, else its best action."""
    a, b = m["start_s"], m["end_s"]
    if m.get("topic") and a <= m["topic"]["at_s"] <= b:
        return m["topic"]["at_s"]
    if m.get("peak_s") is not None and a <= m["peak_s"] <= b:
        return m["peak_s"]
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
    verified = m.get("verified")
    if act:
        items = evidence(rec, *act, verified=verified)
        strong = any(terms.get(k) for k in ("crash", "explosion", "stunt")) or terms.get("activity_label") == "shootout"
        out["action"] = {"window": list(act), "evidence": items,
                         "fits": bool(strong or terms.get("action", 0) >= ACTION_STRONG),
                         "fit": round(terms.get("action", 0) + sum(terms.get(k, 0) for k in ("crash", "explosion",
                                                                                            "stunt")), 2)}
    if nar:
        items = evidence(rec, *nar, verified=verified)
        n = distinct_items(items)
        # A topic candidate (launch-fixes) has its topic as one more thing to show.
        need = NARRATED_ITEMS - 1 if m.get("topic") else NARRATED_ITEMS
        out["narrated"] = {"window": list(nar), "evidence": items, "fits": n >= need, "items": n,
                           "fit": round(0.75 * n + 0.5 * terms.get("star_rise", 0), 2)}
    return out


def choose_types(picks, recs):
    """The type of each pick, with the reason. One that fits only one type gets it; one that fits both gets the
    better fit; one that fits neither gets whatever window it has (ACTION first) and a "weak moment" flag. Then, when
    all picks have one type and a pick could be the other, the pick with the smallest difference changes."""
    for m in picks:
        rec = recs[m["video"]]
        opts = type_options(rec, m)
        m["anchor_s"] = opts["anchor_s"]
        if "safe_options" in m:          # the safety checks dropped a type, or moved its start past gore
            for k in ("action", "narrated"):
                if k in opts and k not in m["safe_options"]:
                    del opts[k]
                elif k in opts and list(m["safe_options"][k]) != list(opts[k]["window"]):
                    opts[k]["window"] = list(m["safe_options"][k])
                    opts[k]["evidence"] = evidence(rec, *m["safe_options"][k], verified=m.get("verified"))
        m["options"] = {k: v for k, v in opts.items() if k in ("action", "narrated")}
        fits = [k for k in ("action", "narrated") if opts.get(k, {}).get("fits")]
        if m.get("topic") and "narrated" in fits:      # topic first (launch-fixes): a topic is taught, narrated
            m["type"], m["type_why"] = "narrated", f"a topic ({m['topic']['id']}): narrated, the footage as proof"
        elif len(fits) == 1:
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


# --- 6b. The story layer (PR 26): what each pick can teach, and the quality gate ---

MISSION_SHARE = 0.3      # subtitles (mission dialogue or objectives) in this share of a moment's sheets: mission footage
LOST_STEADY_S = 3.0      # the stars count as lost when the HUD shows none for this long
# F1 topics: what the moment's evidence must show (words in its lines, or an activity label), and the Rockstar query
# for a dry run's stand-in "GTA 6" side (None: Rockstar's media has no picture of it, so only a real run's GTA 5
# library clip can be the other side).
COMPARE_TOPICS = (
    ("the city at night", ("night",), (), "city skyline night"),
    ("helicopters", ("helicopter",), ("flying",), "helicopter"),
    ("the beach", ("beach",), ("swimming",), "beach"),
    ("sports cars", ("sports car",), (), "sports car"),
    ("police chases", ("police",), ("chase",), None),
)
FIRST_LOOK = ("boat", "flying", "swimming", "chase", "shootout")   # F4: the session's first of these (GTA 6 only)


def mission_share(m, window):
    """The share of the safety sheets over the window that saw subtitles (mission dialogue or objectives)."""
    a, b = window
    sheets = [s for s in (m.get("safety") or {}).get("sheets") or [] if s["from_s"] < b and a < s["to_s"]]
    subs = [s for s in sheets if (s.get("answer") or {}).get("subtitles")]
    return len(subs) / len(sheets) if sheets else 0.0


def stars_lost(rec, after_s, end_s):
    """The first time after after_s (before end_s) that the HUD shows no stars for LOST_STEADY_S, having shown some
    since after_s, or None."""
    count, steady = rec["count"], round(LOST_STEADY_S / STEP)
    i0, i1 = int(after_s / STEP), min(rec["n"], int(end_s / STEP))
    seen = False
    for i in range(i0, i1):
        if count[i]:
            seen = True
        elif seen and i + steady <= rec["n"] and not any(count[i:i + steady]):
            return round(i * STEP, 2)
    return None


def rockstar_standin(query, tags=None):
    """A Rockstar picture for a dry run's stand-in GTA 6 side: the best tag match that is a still with no names
    but Jason's and Lucia's, no paid-edition item and no text overlay; (relative path, its tags) or None."""
    import picker
    import tag_media
    tags = tags if tags is not None else tag_media.load_tags()
    best = None
    for rel, e in tags.items():
        if e.get("kind") != "image" or "Ultimate_Edition" in rel or e.get("overlay") or e.get("type") == "edition":
            continue
        if any(n.lower() not in ("jason", "lucia") for n in e.get("names") or []):
            continue
        hit = picker.match(query, e)
        if hit["matched"] and (best is None or hit["score"] > best[0]):
            best = (hit["score"], rel, e)
    return (best[1], best[2]) if best and best[0] >= 3.0 else None


def compare_side(topic, labels, words, game, dry_run, indexes=None, tags=None, used=()):
    """The other side of an F1 for a topic: in a dry run, Rockstar's official picture (flagged DRYRUN STAND-IN); on
    launch night (GTA 6), the owner's best GTA 5 library clip of the same kind. Evidence ids R1 / C1. None if there
    is none."""
    name, need_words, need_labels, query = topic
    if dry_run:
        if game != "gta5" or not query:
            return None
        found = rockstar_standin(query, tags)
        if not found:
            return None
        rel, e = found
        path = GTA_ROOT / "rockstar" / rel
        return {"topic": name, "side": "GTA 6", "standin": True, "file": str(path), "at": 0,
                "label": "GTA 6", "note": "ROCKSTAR · DRYRUN STAND-IN",
                "evidence": [{"id": "R1", "kind": "rockstar", "value": e.get("description", ""), "from_s": 0.0,
                              "to_s": 0.0, "numbers": [], "picture": str(path),
                              "source": f"Rockstar's official media {rel} (tags: {e.get('main_subject')}; "
                                        f"{', '.join(e.get('subjects', [])[:6])})"}]}
    if game != "gta6" or indexes is None:
        return None
    try:
        rows = library.find(indexes, profile="gta5", min_len=6, **({"activity": list(need_labels)} if need_labels
                                                                     else {"text": list(need_words)}))
    except library.LibraryError:
        return None
    rows = [r for r in rows if not any(v == r["video"] and r["start_s"] < e and s < r["end_s"] for v, s, e in used)]
    if not rows:
        return None
    best = max(rows, key=lambda r: footage_short.score(r, {}, {}, False))
    return {"topic": name, "side": "GTA 5", "standin": False, "file": best["path"], "at": best["start_s"],
            "label": "GTA 5", "note": None,
            "evidence": [{"id": "C1", "kind": "compare", "value": f"GTA 5: {best['activity']}"
                          + "".join(f"; {h['text']}" for h in (best.get("text_hits") or [])[:2]),
                          "from_s": best["start_s"], "to_s": best["end_s"], "numbers": [],
                          "source": f"GTA 5 library: {best['video']} {hms(best['start_s'])}-{hms(best['end_s'])}"}]}


def format_options(rec, m, items, window, game, dry_run, session_firsts=(), compare=None):
    """What a narrated Short of this moment can teach, as formats with their reason. Only from evidence:
      F2 a test whose answer is on the HUD: the wanted stars rise, then are all gone (can you lose them?);
      F3 3 things: three different things the footage shows (a star rise, stars showing, police, crashes ...);
      F1 GTA 5 vs GTA 6: the other game's side of a topic the moment shows (compare);
      F4 first look (GTA 6 only): the session's first boat, flight, swim, chase or shootout."""
    opts = []
    if rec["mode"] == "hud":
        rises = [e for e in items if e["kind"] == "stars" and "at_s" in e]
        for r in rises:
            lost = stars_lost(rec, r["at_s"], window[1])
            if lost:
                opts.append({"format": "F2", "why": f"{r['value']} at {hms(r['at_s'])}, then no stars from {hms(lost)}: "
                                                    f"a test with its answer on the HUD (can you lose them?)",
                             "lost_at_s": lost, "rise": r["id"]})
                break
    n = distinct_items(items)
    if n >= 3:
        things = sorted({e["value"] if e["kind"] != "stars" else ("star rise" if "at_s" in e else "stars showing")
                         for e in items if e["kind"] in ("stars", "police", "event")
                         or (e["kind"] == "activity" and e["value"] not in ("driving", "on_foot", "other"))})
        opts.append({"format": "F3", "why": f"{n} different things on screen: {', '.join(things)}"})
    if compare:
        opts.append({"format": "F1", "why": f"{compare['topic']}: the moment shows it and "
                                            + ("Rockstar's media has it (DRYRUN STAND-IN)" if compare["standin"]
                                               else "the GTA 5 library has a clip of it"), "compare": compare})
    if game == "gta6":
        firsts = [e["value"] for e in items if e["kind"] == "activity" and e["value"] in FIRST_LOOK
                  and (m["video"], e["value"]) in session_firsts]
        if firsts:
            opts.append({"format": "F4", "why": f"the session's first {firsts[0]}"})
    return opts


def session_firsts(recs, recordings):
    """{(recording, activity label): the second it first shows up} for the FIRST_LOOK labels, over the session in
    order: the session's first boat, flight, swim, chase and shootout."""
    out, seen = {}, set()
    for v in recordings:
        rec = recs.get(Path(v).stem)
        if not rec:
            continue
        for s in rec["idx"]["segments"]:
            if s["never_use"] or s["activity"] not in FIRST_LOOK or s["activity"] in seen:
                continue
            seen.add(s["activity"])
            out[(rec["stem"], s["activity"])] = s["start_s"]
    return out


def settle_story_types(picks):
    """The type each pick keeps after the quality gate: a NARRATED Short needs a format, an ACTION CLIP a payoff. A
    pick whose type has neither changes to the other type when that one has it (and is safe); one with neither is
    dropped. F1's other side goes into the pick's evidence (R1 / C1), so lines can cite it."""
    out = []
    for m in picks:
        point = m.get("point")
        if point is None:                            # no gate ran (tests of the older steps)
            out.append(m)
            continue
        want = {"narrated": bool(point["formats"]), "action": bool(point["action_payoff"])}
        if not want.get(m["type"]):
            other = "action" if m["type"] == "narrated" else "narrated"
            chosen = m["options"].get(other)
            if not (want[other] and chosen):
                continue
            m["type_why"] = f"changed to {other}: " + ("no format fits a narrated Short" if other == "action" else
                                                       "an action clip needs a crash, explosion, stunt or star rise")
            m["type"], m["window"], m["evidence"] = other, chosen["window"], chosen["evidence"]
        if m["type"] == "narrated":
            for f in m["format_options"]:
                if f["format"] == "F1":
                    m["evidence"] = m["evidence"] + f["compare"]["evidence"]
        if m.get("mission"):
            m.setdefault("flags", []).append("MISSION FOOTAGE: subtitles in much of it; its point must be clear "
                                             "without the story (shorts.json says why in mission_ok)")
        out.append(m)
    return out


def story_gate(rec, m, game, dry_run, find_compare=None, session_firsts=()):
    """(passes, why) and m's "format_options", "point" and "mission": the owner's quality gate. A moment passes when a
    narrated Short of it can teach something (a format) or an action clip has a payoff (a crash, explosion or stunt,
    or a star rise). Mission footage passes only with a payoff that needs no story: a star rise or one of those
    events."""
    opts = type_options(rec, m)
    for k in ("action", "narrated"):           # as choose_types() will: the safety checks' windows
        if k in opts and "safe_options" in m:
            if k not in m["safe_options"]:
                del opts[k]
            elif list(m["safe_options"][k]) != list(opts[k]["window"]):
                opts[k]["window"] = list(m["safe_options"][k])
                opts[k]["evidence"] = evidence(rec, *m["safe_options"][k], verified=m.get("verified"))
    nar = opts.get("narrated")
    formats = []
    if nar:
        words = " ".join(str(e["value"]).lower() for e in nar["evidence"] if not e.get("not_seen"))    # (PR 35)
        labels = {e["value"] for e in nar["evidence"] if e["kind"] == "activity"}
        compare = None
        for topic in COMPARE_TOPICS:
            if any(w in words for w in topic[1]) or labels & set(topic[2]):
                compare = (find_compare or (lambda t, l, w: None))(topic, labels, words)
                if compare:
                    break
        formats = format_options(rec, m, nar["evidence"], nar["window"], game, dry_run, session_firsts, compare)
        if m.get("topic"):                       # launch-fixes, topics first: the topic is the point (engine v3)
            formats.append({"format": "T1", "why": f"a topic ({m['topic']['id']}): {m['topic']['why']}"})
    act = opts.get("action")
    payoff = []
    if act:
        payoff = sorted({e["value"] for e in act["evidence"] if e["kind"] == "event"
                         and e["value"] in ("crash", "explosion", "stunt")}
                        | {"star rise" for e in act["evidence"] if e["kind"] == "stars" and "at_s" in e})
    window = (nar or act or {"window": [m["start_s"], m["end_s"]]})["window"]
    m["mission"] = mission_share(m, window) >= MISSION_SHARE
    m["format_options"], m["point"] = formats, {"formats": [f["format"] for f in formats], "action_payoff": payoff}
    if m["mission"]:
        clear = payoff or any(f["format"] == "F2" for f in formats)
        if not clear:
            return False, ["mission footage with no point that is clear without the story (no star rise, test or "
                           "crash/explosion/stunt)"]
    if not formats and not payoff:
        return False, ["no clear point: nothing to teach (no format fits) and no payoff for an action clip"]
    return True, []


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


def hud_status(choice, modes, checks, hud_log):
    """How the HUD was read over the whole run, for the top of candidates.json and review.html: "hud" (every recording
    read with the profile), "partial" (some went NO-HUD, each with why) or "nohud" (none could be: the reason), and
    where the full log is. Never silent (launch-fixes)."""
    nohud = [stem for stem, m in modes.items() if m == "nohud"]
    mode = "nohud" if choice["mode"] == "nohud" or (modes and len(nohud) == len(modes)) else "partial" if nohud else "hud"
    return {"mode": mode, "profile": choice["profile"], "why": choice["why"], "log": str(hud_log),
            "nohud_recordings": {stem: "; ".join((checks.get(stem) or {}).get("reasons") or [choice["why"]])
                                 for stem in nohud}}


def index_recording(v, choice, game, every, lib_dir, cache_dir, do_index, log=print, hlog=print):
    """One recording indexed with the chosen profile (choose_profile), its HUD reading checked against the model
    (hud_check); one whose HUD can't be trusted is indexed again with the NO-HUD profile. Returns (index, "hud" or
    "nohud", the HUD check or None). Shared by prepare and the content bank (bank.py)."""
    idx = do_index(v, profile=choice["profile"], every=every, library=lib_dir, cache_dir=cache_dir,
                   log=lambda m: log("  " + m))
    idx["_dir"] = str(Path(lib_dir) / v.stem)
    mode, checked = choice["mode"], None
    if mode == "hud":
        try:
            tl = library.timeline_of(idx)
            ok, reasons, numbers = hud_check(idx, tl)
        except library.LibraryError as e:
            ok, reasons, numbers = False, [str(e)], {}
        checked = {"ok": ok, "reasons": reasons, "numbers": numbers}
        hlog(f"  {v.name}: HUD check {'passed' if ok else 'FAILED'} {numbers}"
             + (f": {'; '.join(reasons)}" if reasons else ""))
        if not ok:
            hlog(f"  The HUD reading of {v.name} can't be trusted ({'; '.join(reasons)}): NO-HUD MODE for it.")
            mode = "nohud"
            idx = do_index(v, profile=f"{game}_nohud", every=every, library=lib_dir, cache_dir=cache_dir,
                           log=lambda m: log("  " + m))
            idx["_dir"] = str(Path(lib_dir) / v.stem)
    return idx, mode, checked


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
    if model in info["loaded"]:
        # launch-fixes: a render minutes ago can leave our own model loaded (keep_alive), holding memory the free
        # memory check below would blame on other programs. It loads again on the first call.
        vlm.unload(model)
    free = index.free_ram_gb()
    if free is not None and free < index.RELOAD_BELOW_GB:
        raise LaunchError(f"only {free:.1f} GB of memory is free; close other programs (a browser with many tabs, "
                          "ComfyUI) and type /launch again")
    return free


def failed_renders(shorts_dir=SHORTS_DIR):
    """[{video, from, to, where, why}]: every Short of an earlier run whose last render FAILED on its pictures (the
    shot check: rejected_shots), with its window. The Nov 20 re-pick's M21 was part2's Short 4 again: the jet ski over
    an empty ocean that the shot check had refused. The picker can't see that by itself, so it is read here."""
    out = []
    for f in sorted(Path(shorts_dir).glob("*/" + RENDER_LOG)):
        for e in (read_json(f) or {}).get("shorts", []):
            rejected = (e.get("checks") or {}).get("rejected_shots") or []
            if e.get("status") != "FAILED" or not rejected or not e.get("video") or not e.get("window"):
                continue
            out.append({"video": e["video"], "from": float(e["window"][0]), "to": float(e["window"][1]),
                        "where": f"{f.parent.name} Short {e.get('short')}",
                        "why": "; ".join(rejected[0].get("why") or [])[:200]})
    return out


def failed_before(m, failed):
    """The earlier failed render this moment mostly repeats (FAILED_SHARE of its seconds or more), or None."""
    length = max(1e-6, m["end_s"] - m["start_s"])
    return next((f for f in failed if f["video"] == m["video"]
                 and overlap(m["start_s"], m["end_s"], f["from"], f["to"]) >= FAILED_SHARE * length), None)


def pick_step(recs, recordings, used, posted, safety, folder, game, count, dry_run, topics=True, log=print,
              hud6=None, failed=(), moments=None):
    """prepare's step 5, shared with repick: mine every recording's moments and the topic candidates (leaving out
    used footage), check a pool of them (safety, labels against the frames, the quality gate) and pick `count`
    (pick_varied), then give each pick its type and the GTA 6 HUD's close look. Returns (moments, picks, rejected,
    pool). safety: a callable (m, rec, folder) -> (passes, why), or None to skip it. hud6: the GTA 6 HUD reader
    (proof.hud6 by default; tests pass a stand-in). failed: failed_renders(); a moment that mostly repeats one is left
    out before the pool, with why. moments: the content bank's candidates (bank.candidates, PR 32) instead of mining
    here: only new and rendered ones, labelled from their peaks."""
    if moments is None:
        moments = [m for rec in recs.values() for m in mine(rec, used)]
        moments += topic_candidates(recs, used, hud6=hud6, log=log) if topics else []
    else:
        moments = list(moments)
    moments.sort(key=lambda m: -m["score"])
    for k, m in enumerate(moments, 1):
        m["id"] = f"M{k}"
    left_out, kept = [], []
    for m in moments:
        bad = failed_before(m, failed)
        if bad:
            why = f"failed the shot check when it was rendered before ({bad['where']}: {bad['why']})"
            left_out.append({"id": m["id"], "video": m["video"], "start_s": m["start_s"], "end_s": m["end_s"],
                             "score": m["score"], "category": m.get("category"), "why": [why]})
            log(f"  Left out {m['id']} {m['video']} {hms(m['start_s'])}-{hms(m['end_s'])}: {why}")
        else:
            kept.append(m)
    moments = kept
    log(f"Moments: {len(moments)} found; the best: "
        + "; ".join(f"{m['id']} {m['video']} {hms(m['start_s'])} {m['category']} {m['score']:.1f}" for m in moments[:5]))
    safe = (lambda m: safety(m, recs[m["video"]], folder)) if safety else (lambda m: (True, []))
    firsts = session_firsts(recs, recordings)
    found = {}

    def find_compare(topic, labels, words):
        if topic[0] not in found:
            main = None
            if game == "gta6" and not dry_run:
                try:
                    main = library.load_library(index.LIBRARY)
                except library.LibraryError:
                    main = None
            found[topic[0]] = compare_side(topic, labels, words, game, dry_run, main, used=used)
        return found[topic[0]]

    def ok(m):
        """The safety checks, the labels checked against what the frames show (launch-fixes), then the owner's
        quality gate (PR 26): a moment with no clear point is skipped."""
        passes, why = safe(m)
        if not passes:
            return passes, why
        if m.get("bank_id"):                    # an impact counts only where the moment's peak shows it
            import bank
            bank.keep_what_the_peak_shows(m)
        verify_terms(recs[m["video"]], m)
        if m.get("bank_id"):                    # ...and is what the moment is about, whatever else is in it
            m["category"] = bank.impact_of(m) or m["category"]
        return story_gate(recs[m["video"]], m, game, dry_run, find_compare, firsts)
    log(f"Checking a pool of up to {max(POOL_MIN, min(POOL_MAX, 5 * count))} candidates for {count} Short(s)"
        + (f", {len(posted)} posted topic(s) counted" if posted else "") + "...")
    picks, rejected, pool = pick_pool(moments, ok, count=count, log=log, posted=posted,
                                      text_of=lambda m: moment_text(recs[m["video"]], m))
    rejected = left_out + rejected
    picks = settle_story_types(choose_types(picks, recs))
    for m in picks:
        try:
            m["evidence"] = m["evidence"] + hud6_evidence(recs[m["video"]], m, hud6)
        except Exception as e:                       # a close look that fails must not stop the night
            log(f"  Note: the GTA 6 HUD look at {m['video']} failed ({e}); its pick keeps the index's evidence")
    return moments, picks, rejected, pool


def prepare(game="gta6", profile=None, videos=None, every=index.EVERY_S, dry_run=False, input_dir=INPUT, count=3,
            topics=True,
            shorts_dir=SHORTS_DIR, library_dir=index.LIBRARY, day=None, slots=None, safety="auto", log=print,
            index_video=None, guided_run=None, clip_runner=None, check=True, sheets=True, posted_file=POSTED_FILE,
            bank_dir=None, hud6=None):
    """The prepare stage; returns the run folder (with candidates.json). safety "auto" asks the safety question
    through Ollama (safety.Checker), None skips it. index_video, guided_run, clip_runner and safety are swapped for
    stand-ins by the tests; check=False skips the Ollama/memory pre-flight. bank_dir (PR 32): the picks come from the
    content bank there, brought up to date with the new recordings: every recording's moments, new and rendered ones
    (never posted or rejected ones); no new recording is no reason to stop then. Without it (a dry run) the new
    recordings alone are mined, as before."""
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
                                                      if v.suffix.lower() in index.VIDEO_EXTS] if dry_run else None), game)
    # launch-fixes: a recording too short for a Short (launch night had a 14.9 s one) is left out with a plain line,
    # before anything else looks at it.
    durations = {v.stem: probe_duration(v) for v in recordings}
    for v in recordings:
        if durations[v.stem] < MIN_RECORDING_S:
            skipped[v.name] = (f"only {durations[v.stem]:.1f} s long; a Short needs a recording of "
                               f"{MIN_RECORDING_S:g} s or more")
    recordings = [v for v in recordings if durations[v.stem] >= MIN_RECORDING_S]
    for name, why in skipped.items():
        log(f"  Skipped {name}: {why}")
    use_bank = bank_dir is not None and not dry_run
    if not recordings and not (use_bank and load_indexed(library_dir)):
        raise LaunchError(f"no new recordings in {input_dir}. Copy the PS5 files there first (every file already "
                          "in the library or taken by an earlier /launch run is skipped, and so is one under "
                          f"{MIN_RECORDING_S:g} s)")
    if not recordings:
        log("No new recordings: the picks come from the content bank (every recording so far)")
    folder = run_folder(shorts_dir, day, runs, dry_run)
    resumed = not dry_run and any(Path(r["folder"]) == folder and r.get("status") == "preparing" for r in runs["runs"])
    folder.mkdir(parents=True, exist_ok=True)
    log(f"Run folder: {folder}" + (" (a stopped run: it carries on where it stopped; the model's answers so far and "
                                   "the finished clip.py runs are used again)" if resumed else ""))
    log(heavy.ram_line())
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
    log(f"Recordings ({len(recordings)}, {sum(durations.values()) / 60:.1f} min): "
        + ", ".join(f"{v.name} ({hms(durations[v.stem])})" for v in recordings))

    # 2. The profile. Every HUD step goes to logs/hud.txt too (launch-fixes: launch night's calibration failed and
    # the run said only "could not start or save a profile").
    t = time.perf_counter()
    (folder / "logs").mkdir(exist_ok=True)
    hud_log = folder / "logs" / "hud.txt"

    def hlog(m):
        log(m)
        with open(hud_log, "a", encoding="utf-8") as fh:
            fh.write(m + "\n")
    hlog(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}: HUD for {len(recordings)} recording(s), profile {profile} ---")
    choice = (choose_profile(profile, game, recordings, durations, slots or GAMES[game]["slots"], hlog, guided_run)
              if recordings else {"profile": profile, "mode": "hud", "stars": True, "attempts": [],
                                  "why": "no new recordings to read"})
    timing["calibration"] = round(time.perf_counter() - t, 1)
    hlog(f"Profile: {choice['profile']} ({'HUD rules on' if choice['mode'] == 'hud' else 'NO-HUD MODE'}): "
         f"{choice['why']}")

    # 3. Index, with clip.py running alongside.
    log(heavy.ram_line() + " (before the index)")
    t = time.perf_counter()
    runner = clip_runner or (lambda v, p: run_clip(v, p, folder / "clip_runs", folder / "logs"))
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    clip_jobs = {v.stem: pool.submit(runner, v, choice["profile"]) for v in recordings}
    do_index = index_video or index.index_video
    indexes, modes, checks = {}, {}, {}
    try:
        for v in recordings:
            log(f"Indexing {v.name} with {choice['profile']}...")
            indexes[v.stem], modes[v.stem], check_ = index_recording(v, choice, game, every, lib_dir, cache_dir, do_index,
                                                                     log, hlog)
            if check_ is not None:
                checks[v.stem] = check_
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
    log(heavy.ram_line() + " (before the safety checks)")
    t = time.perf_counter()
    used = [] if dry_run else [tuple(s) for s in all_used_spans(shorts_dir)]
    posted = load_posted(posted_file, log=log)
    recs, offset = {}, 0.0
    for v in recordings if not use_bank else []:
        idx = indexes[v.stem]
        act = action_series(clip_runs.get(v.stem), math.ceil(idx["video"]["duration_s"] / STEP))
        recs[v.stem] = recording_data(v.stem, idx, act, modes[v.stem], offset)
        offset += idx["video"]["duration_s"]
    if safety == "auto":
        safety = safety_checks.Checker(GAMES[game]["name"], Path(library_dir) / "_cache", log=log)
    from_bank, session, banked = None, list(recordings), None
    if use_bank:
        import bank
        recs = bank.load_recs(lib_dir, shorts_dir, bank_dir, hud6=hud6, log=log, game=game)
        banked = bank.build(lib_dir, shorts_dir, bank_dir, safety if hasattr(safety, "look") else None, recs=recs, log=log)
        from_bank = bank.candidates(banked["moments"], recs, bank.status_spans(shorts_dir))
        session = [Path(r["path"]) for r in recs.values()]
        log(f"Content bank: {len(from_bank)} of {len(banked['moments'])} moments may be picked (not posted or rejected)")
    moments, picks, rejected, pool = pick_step(recs, session, used, posted, safety, folder, game, count, dry_run,
                                               topics, log, hud6, [] if dry_run else failed_renders(shorts_dir), from_bank)
    if use_bank:                                 # the run's recordings: the new ones, and those its picks come from
        recordings = recordings + [v for v in session if v.stem in {m["video"] for m in picks}
                                   and v.stem not in {r.stem for r in recordings}]
        for v in recordings:
            if v.stem not in indexes:
                indexes[v.stem], modes[v.stem] = recs[v.stem]["idx"], recs[v.stem]["mode"]
                durations[v.stem] = recs[v.stem]["duration_s"]
                clip_runs[v.stem] = bank.newest_clip_run(v.stem, shorts_dir, bank_dir)
    timing["mine_and_safety"] = round(time.perf_counter() - t, 1)
    if isinstance(safety, safety_checks.Checker):
        timing["safety_model"] = round(safety.seconds, 1)
        log(f"Safety checks: {safety.calls} model call(s), {safety.cached} cached, {safety.seconds:.0f} s"
            + (f", the model unloaded {safety.reloads} time(s) for memory" if safety.reloads else ""))
        safety_checks.vlm.unload(safety.model)
    if not picks:
        if use_bank:                     # the recordings are in the bank now: the next run needn't carry this one on
            note_run(shorts_dir, folder, recordings, "nothing picked", dry_run)
        raise LaunchError(f"no moment passed the checks with a payoff of {PAYOFF_BAR}/3 or more: nothing to make a "
                          "Short from (candidates.json's pool says why each was left out)")
    if sheets:
        (folder / "moments").mkdir(exist_ok=True)
        for m in picks:
            out = folder / "moments" / f"{m['id']}.jpg"
            m["sheet"] = str(out) if moment_sheet(m["path"], m["window"][0], m["window"][1], out) else None
    for n, m in enumerate(picks, 1):
        m["short"] = n
        m.setdefault("flags", []).extend((m.get("safety") or {}).get("flags", []))
        if m["type"] == "action":
            m["peak_options"] = peak_options(recs[m["video"]], m, folder, strips=sheets)
        if m["mode"] == "nohud":
            m.setdefault("flags", []).append("NO-HUD MODE: no wanted-star counts; narration may not mention stars")
    timing["total"] = round(time.perf_counter() - t_start, 1)
    log(heavy.ram_line() + " (end of prepare)")
    data = {"version": 1, "created": datetime.now().isoformat(timespec="seconds"), "dry_run": dry_run,
            "game": game, "game_name": GAMES[game]["name"], "folder": str(folder),
            "recordings": [{"name": v.name, "stem": v.stem, "path": str(v), "duration_s": round(durations[v.stem], 2),
                            "session_offset_s": round(recs[v.stem]["offset_s"], 2), "mode": modes[v.stem],
                            "profile": indexes[v.stem]["profile"]["name"], "hud_check": checks.get(v.stem),
                            "clip_run": str(clip_runs.get(v.stem)) if clip_runs.get(v.stem) else None}
                           for v in recordings],
            "session_s": round(offset or sum(r["duration_s"] for r in recs.values()), 2), "skipped": skipped, "calibration": choice,
            "hud": hud_status(choice, modes, checks, hud_log),
            "settings": {"every_s": every, "window_s": WINDOW_S, "library": str(lib_dir)},
            "moments_found": len(moments),
            "top_moments": [{k: m[k] for k in ("id", "video", "start_s", "end_s", "session_s", "score", "category",
                                               "terms")} for m in moments[:10]],
            "rejected": rejected,
            "count": count,
            "pool": [{"id": m.get("id"), "bank_id": m.get("bank_id"), "status": m.get("status"),
                      "label": (m.get("label") or {}).get("name"), "video": m["video"], "start_s": m["start_s"],
                      "end_s": m["end_s"], "score": m["score"], "category": m.get("category"), "terms": m.get("terms"),
                      "dropped_labels": m.get("dropped_labels", []), "checked": m.get("checked"),
                      "payoff_strength": m.get("payoff_strength"), "repeats": m.get("repeats", [])} for m in pool],
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


def load_indexed(library_dir):
    """The recordings the library has an index of."""
    return list((read_json(Path(library_dir) / "library.json") or {}).get("videos") or {})


# --- repick (PR 29): the picking step again on an old run's cached data ---

def used_as_of(shorts_dir, run_name):
    """The used-footage spans as they were when the run `run_name` was prepared: the entries before the first Short
    made by that run (or its -partN re-runs), and none after it."""
    base = re.sub(r"-part\d+$", "", run_name)
    out = []
    for s in footage_short.load_used(shorts_dir)["shorts"]:
        if f"@{base}" in str(s.get("name", "")):
            break
        if footage_short.locked(s):
            out += [tuple(span) for span in s.get("spans", [])]
    return out


def pick_line(m):
    ps = m.get("payoff_strength") or payoff_strength(m)
    return (f"{moment_name(m)} {m['video'][-6:]} {hms(m['start_s'])}-{hms(m['end_s'])} {m.get('category')} "
            f"(score {m['score']:.2f}, payoff {ps['tier'] if isinstance(ps, dict) else ps[0]}/3)")


def repick(run, count=None, posted_now=False, shorts_dir=SHORTS_DIR, safety="auto", out=None, log=print, hud6=None,
           bank_dir=None):
    """The picking step of `run` again, with today's rules, on its cached data: each recording's index.json from the
    library and its clip.py run (nothing is indexed again), the used footage and the posted topics as they were when
    the run was prepared (posted_now: today's topics and all the footage used since). The safety answers come from the model's cache where it has
    them. Writes repick.json and repick.txt (old picks against new ones) in `out` (default <run>-repick next to it);
    the run folder, _used_footage.json and _launch_runs.json are left alone. bank_dir (PR 32): the candidates are the
    content bank's moments of the run's recordings, labelled from their peaks (with the footage locked then). Returns the
    data."""
    run = Path(run)
    cand = read_json(run / "candidates.json")
    if not cand:
        raise LaunchError(f"{run} has no candidates.json")
    count = count or cand.get("count", 3)
    game = cand.get("game", "gta6")
    lib = Path(cand["settings"]["library"])
    out = Path(out) if out else run.with_name(run.name + "-repick")
    out.mkdir(parents=True, exist_ok=True)
    recs, recordings = {}, []
    for r in cand["recordings"]:
        idx = read_json(lib / r["stem"] / "index.json")
        if not idx:
            raise LaunchError(f"no cached index for {r['stem']} in {lib}: repick doesn't index anything")
        idx["_dir"] = str(lib / r["stem"])
        act = action_series(r.get("clip_run"), math.ceil(idx["video"]["duration_s"] / STEP))
        recs[r["stem"]] = recording_data(r["stem"], idx, act, r["mode"], r["session_offset_s"])
        recordings.append(Path(r["path"]))
    # posted_now: today's view, so the footage used since (the Shorts made from this run too) is used as well
    used = [tuple(sp) for sp in all_used_spans(shorts_dir)] if posted_now else used_as_of(shorts_dir, run.name)
    posted = load_posted(POSTED_FILE, None if posted_now else cand["created"], log)
    log(f"Re-picking {run.name} ({len(recs)} recordings, {count} picks): {len(used)} used span(s) and "
        f"{len(posted)} posted topic(s) " + ("as of today" if posted_now else f"as of {cand['created']}"))
    if safety == "auto":
        safety = safety_checks.Checker(GAMES[game]["name"], lib / "_cache", log=log)
    failed = failed_renders(shorts_dir)
    from_bank = None
    if bank_dir is not None:
        import bank
        stems = {r["stem"] for r in cand["recordings"]}
        recs = bank.load_recs(lib, shorts_dir, bank_dir, stems, hud6, log, game)
        found, _, _, _ = bank.make_moments(recs, safety if hasattr(safety, "look") else None, lib / "_cache", out / "peaks",
                                           log=log)
        spans = {"posted": [(*u, "") for u in used], "rendered": [], "rejected": []}
        from_bank = bank.candidates(found, recs, bank.status_spans(shorts_dir) if posted_now else spans)
    _, picks, rejected, pool = pick_step(recs, recordings, used, posted, safety, out, game, count,
                                         cand.get("dry_run", False), True, log, hud6, failed, from_bank)
    calls = {}
    if isinstance(safety, safety_checks.Checker):
        calls = {"model_calls": safety.calls, "cached": safety.cached, "seconds": round(safety.seconds, 1)}
        safety_checks.vlm.unload(safety.model)
    old = cand.get("picks", [])
    data = {"run": str(run), "created": datetime.now().isoformat(timespec="seconds"), "count": count,
            "posted_as_of": None if posted_now else cand["created"], "posted": [t["name"] for t in posted],
            "used_spans": len(used), "safety": calls,
            "old_picks": [{k: m.get(k) for k in ("id", "video", "start_s", "end_s", "score", "category", "type")}
                          for m in old],
            "new_picks": [{k: m.get(k) for k in ("id", "video", "start_s", "end_s", "score", "category", "type",
                                                 "payoff_strength", "repeats", "pick_why", "window", "bank_id",
                                                 "label")} for m in picks],
            "pool": [{"id": m.get("id"), "video": m["video"], "start_s": m["start_s"], "score": m["score"],
                      "category": m.get("category"), "payoff_strength": m.get("payoff_strength"),
                      "repeats": m.get("repeats", []), "checked": m.get("checked")} for m in pool],
            "rejected": rejected}
    write_json(out / "repick.json", data)
    lines = [f"Re-pick of {run.name}: {count} picks, posted topics as of "
             f"{'today' if posted_now else cand['created']}, safety answers {calls or 'not asked'}", "",
             "OLD PICKS (as made that night):"]
    lines += [f"  {k}. {pick_line(m)}" for k, m in enumerate(old, 1)]
    lines += ["", "NEW PICKS (payoff first, no near-duplicates, both types, posted topics cost):"]
    for k, m in enumerate(picks, 1):
        lines += [f"  {k}. {pick_line(m)} -> {m.get('type', '').upper()}", f"     why: {m.get('pick_why')}"]
    early = [r for r in rejected if any("rendered before" in w for w in r.get("why", []))]
    if early:
        lines += ["", "LEFT OUT BEFORE THE POOL (failed the shot check at an earlier render):"]
        lines += [f"  {r['id']} {r['video'][-6:]} {hms(r['start_s'])}-{hms(r['end_s'])}: {r['why'][0]}" for r in early]
    lines += ["", "THE REST OF THE POOL:"]
    for m in pool:
        c = m.get("checked") or {}
        if m in picks:
            continue
        lines.append(f"  {pick_line(m)}: " + (c.get("not_picked") or "; ".join(c.get("why", [])) or "not checked"))
    (out / "repick.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines:
        log(line)
    return data


# ======================================================================================================================
# render: Claude's shorts.json -> checked, rendered Shorts, upload.txt, claims.txt and review.html
# ======================================================================================================================

GTA_ROOT = footage_short.make_short.GTA_ROOT
SHORTS_FILE = "shorts.json"              # in the run folder: what Claude wrote
RENDER_LOG = "render.json"               # in the run folder: what render made of it
END_PILL = "@NeonGetaway • Subscribe"
NARRATED_LENGTH = (20.0, 35.0)
GAME_LUFS, MUSIC_LUFS = -14.0, -28.0     # an ACTION CLIP: the game's sound (as loud as every Short), the music bed 14 LU under it
MIX_LIMIT_DB = -1.5                      # the final mix goes through a true-peak limiter at this; the guard wants < -1
MUSIC_FADE_IN_S, MUSIC_FADE_OUT_S = 0.5, 1.0
PEAK_EVERY_S = 0.2                       # a peak strip: a frame every 0.2 s round the moment Claude picks the hit from
PEAK_SLACK_S = 1.0                       # an AUTO PEAK must lie this close to the evidence it names
HOOK_SECONDS = 3.0


class RenderProblem(Exception):
    """One Short can't be rendered as written; the text lists every problem (Claude fixes shorts.json and reruns)."""


def peak_strip(path, a, b, out, every=PEAK_EVERY_S, cols=6, width=320):
    """Frames every `every` s from a to b of a recording, each with its time on it: Claude picks the hit from it."""
    return moment_sheet(path, a, b, out, every=every, cols=cols, width=width)


def peak_options(rec, m, folder, strips=True):
    """Where an ACTION CLIP's auto peak may be, from evidence only (the owner's rule): each star rise (the exact half
    second the HUD changed) and each crash, explosion or stunt sheet, with a strip of frames 0.2 s apart round it.
    Never clip.py's peak_time_s."""
    out = []
    w0, w1 = m["window"]
    for e in m["evidence"]:
        if e["kind"] == "stars" and "at_s" in e:
            a, b = e["at_s"] - 1.5, e["at_s"] + 1.5
        elif e["kind"] == "event" and e["value"] in ("crash", "explosion", "stunt"):
            a, b = e["from_s"], e["to_s"]
        else:
            continue
        a, b = max(w0, a), min(w1, b)
        if b - a < 1.0:
            continue
        opt = {"evidence": e["id"], "kind": e["value"] if e["kind"] == "event" else "star rise", "from_s": round(a, 2),
               "to_s": round(b, 2), "every_s": PEAK_EVERY_S, "at_s": e.get("at_s")}
        if strips:
            strip = Path(folder) / "moments" / f"{m['id']}_peak_{e['id']}.jpg"
            opt["strip"] = str(strip) if peak_strip(m["path"], a, b, strip) else None
        out.append(opt)
    return out


def grid_frame(path, at, out, width=960):
    """One frame of a recording at `at` seconds, 960 px wide, with a grid every 192 px of the recording (a tenth of
    its width) and every 108 px down, each line labelled in the recording's own pixels: Claude reads a circle's box
    or a freeze's detail off it (x, y, w, h in those pixels)."""
    font = "C\\:/Windows/Fonts/arial.ttf"
    k = width / 1920
    labels = "".join(f",drawtext=fontfile='{font}':text='{x}':x={x * k + 3:.0f}:y=3:fontsize=16:fontcolor=yellow:"
                     f"box=1:boxcolor=black@0.6" for x in range(192, 1920, 192))
    labels += "".join(f",drawtext=fontfile='{font}':text='{y}':x=3:y={y * k + 3:.0f}:fontsize=16:fontcolor=yellow:"
                      f"box=1:boxcolor=black@0.6" for y in range(108, 1080, 108))
    graph = (f"scale={width}:-2,drawgrid=w={192 * k:.0f}:h={108 * k:.0f}:t=1:c=yellow@0.5{labels},"
             f"drawtext=fontfile='{font}':text='{at:.2f} s':x=w-tw-6:y=h-th-6:fontsize=22:fontcolor=white:box=1:"
             f"boxcolor=black@0.7")
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at:.3f}", "-i", str(path),
                           "-frames:v", "1", "-vf", graph, "-q:v", "3", str(out)], capture_output=True, text=True)
    return done.returncode == 0 and Path(out).is_file()


def latest_run(shorts_dir=SHORTS_DIR):
    """The newest run folder that has candidates.json: the last one in _launch_runs.json, else the newest on disk."""
    runs = [Path(r["folder"]) for r in load_runs(shorts_dir)["runs"] if r.get("status") in ("prepared", "rendered")]
    runs = [r for r in runs if (r / "candidates.json").is_file()]
    if runs:
        return runs[-1]
    found = sorted(Path(shorts_dir).glob("launch-*/candidates.json"), key=lambda p: p.stat().st_mtime)
    if not found:
        raise LaunchError(f"no /launch run in {shorts_dir}: run launch.py prepare first")
    return found[-1].parent


def moment_for(cand, short):
    for m in cand["picks"]:
        if short.get("short") == m["short"] or (short.get("moment") and short["moment"] == m["id"]):
            return m
    raise RenderProblem(f"short {short.get('short')}: no pick with that number (or moment id) in candidates.json")


def check_peak(short, m, no_cold_open):
    """(peak in the recording's seconds or None, problems). The peak must name an evidence item of the moment's
    peak_options and lie within PEAK_SLACK_S of it, inside the clip."""
    peak = short.get("peak")
    if no_cold_open or peak is None:
        return None, []
    if not isinstance(peak, dict) or not isinstance(peak.get("at_s"), (int, float)) or isinstance(peak.get("at_s"), bool):
        return None, ["peak: {\"evidence\": \"E4\", \"at_s\": seconds in the recording}, or null for no cold open"]
    at = float(peak["at_s"])
    opts = [o for o in m.get("peak_options", []) if o["evidence"] == peak.get("evidence")]
    if not opts:
        names = ", ".join(o["evidence"] for o in m.get("peak_options", [])) or "none: this moment gets no cold open"
        return None, [f"peak: evidence {peak.get('evidence')!r} is not one of this moment's peak options ({names})"]
    o = opts[0]
    w0, w1 = m["window"]
    if not (o["from_s"] - PEAK_SLACK_S <= at <= o["to_s"] + PEAK_SLACK_S and w0 + 0.5 <= at <= w1 - 0.8):
        return None, [f"peak: {at:.1f} s is not within {PEAK_SLACK_S:g} s of {o['evidence']} "
                      f"({o['from_s']:.1f}-{o['to_s']:.1f} s) inside the clip ({w0:.1f}-{w1:.1f} s)"]
    return round(at, 2), []


def trimmed(m, start):
    """(the moment with its footage starting at `start` instead, problems). shorts.json may start a Short later
    inside its window (to skip a swear word in the subtitles or a dull opening), keeping ACTION_LEN[0] / NARRATED_MIN_S
    of footage; evidence and peak options that end before the new start drop out (their ids stay the same)."""
    w0, w1 = m["window"]
    need = ACTION_LEN[0] if m["type"] == "action" else NARRATED_MIN_S
    if isinstance(start, bool) or not isinstance(start, (int, float)) or not w0 <= start <= w1 - need:
        return m, [f"start_s: {start!r} must be a time from {w0:.1f} to {w1 - need:.1f} s of the recording (the Short "
                   f"keeps {need:g} s of footage or more)"]
    start = round(float(start), 2)
    return dict(m, window=[start, w1], evidence=[e for e in m["evidence"] if e["to_s"] > start],
                peak_options=[o for o in m.get("peak_options", []) if o["to_s"] > start + 0.5]), []


def tools():
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise LaunchError("ffmpeg and ffprobe must be on PATH")
    return ffmpeg, ffprobe


def pick_music(seconds, seed):
    import explainer
    music, note = explainer.pick_music("auto", GTA_ROOT, seconds, seed)
    return (Path(music) if music else None), note


def mix_music(src, music, dst, seconds, has_game):
    """dst = src's picture as it is, with the game's sound at GAME_LUFS and a music bed MUSIC_LUFS under it (faded in
    and out), through the true-peak limiter. No music: the game alone; no game sound: the music alone, a little up."""
    inputs = ["-i", str(src)] + (["-stream_loop", "-1", "-i", str(music)] if music else [])
    fade = (f"afade=t=in:d={MUSIC_FADE_IN_S:g},afade=t=out:st={max(0.0, seconds - MUSIC_FADE_OUT_S):.3f}:"
            f"d={MUSIC_FADE_OUT_S:g}")
    fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
    parts = []
    if has_game:
        parts.append(f"[0:a]{fmt},loudnorm=I={GAME_LUFS:g}:TP=-2:LRA=11,{fmt}[g]")
    if music:
        level = MUSIC_LUFS if has_game else GAME_LUFS - 4
        parts.append(f"[1:a]{fmt},atrim=0:{seconds:.3f},loudnorm=I={level:g}:TP=-2:LRA=11,{fmt},{fade}[m]")
    if has_game and music:
        parts.append(f"[g][m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                     f"{safety_checks.limiter(MIX_LIMIT_DB)}[a]")
    elif has_game or music:
        parts.append(f"[{'g' if has_game else 'm'}]{safety_checks.limiter(MIX_LIMIT_DB)}[a]")
    else:
        shutil.copyfile(src, dst)
        return
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(parts), "-map", "0:v",
                           "-c:v", "copy", "-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                           "-t", f"{seconds:.3f}", "-movflags", "+faststart", str(dst)], capture_output=True, text=True)
    if done.returncode:
        raise RenderProblem(f"ffmpeg could not mix the music in: {done.stderr.strip()[-400:]}")


def render_action(short, m, out_dir, hook, peak, log=print):
    """An ACTION CLIP: the window cut to 1080x1920 exactly as clip.py cuts its clips, then hooks.py's banner (3 s),
    cold open and punch-in zoom at the auto peak, then the music bed under the game's sound and the limiter. Returns
    what was done."""
    import hooks
    ffmpeg, ffprobe = tools()
    video = Path(m["path"])
    info = clip.probe(ffprobe, video)
    range_fix = clip.needs_range_fix(info["video_codec"], info["color_range"])
    encoder, enc_args = clip.pick_encoder(ffmpeg)
    w0, w1 = m["window"]
    raw = out_dir / "clip_01.mp4"
    clip.render(ffmpeg, video, {"start_s": w0, "duration_s": round(w1 - w0, 3)}, raw, False, enc_args,
                info["has_audio"], range_fix)
    typed = hook + (f" | peak: {peak - w0:.2f}" if peak is not None else "")
    opts = {"style": hooks.DEFAULT_STYLE, "seconds": HOOK_SECONDS, "cold_open": True, "zoom": True}
    try:
        plan = hooks.plan_clip({"rank": 1, "file": raw.name}, typed, out_dir, ffmpeg, ffprobe, opts)
        hooks.burn_clip(plan, out_dir, ffmpeg, enc_args)
    except hooks.HookError as e:
        raise RenderProblem(f"the hook could not be burned in: {e}") from None
    final = out_dir / f"{plan['cid']}_final.mp4"
    music, note = pick_music(plan["final_s"], short.get("title", ""))
    mix_music(final, music, out_dir / "short.mp4", plan["final_s"], plan["has_game"])
    for f in (raw, final, out_dir / f"{plan['cid']}_hook.ass"):
        f.unlink(missing_ok=True)
    return {"length_s": round(plan["final_s"], 2), "cold_open": plan["cold"], "zoom_at_s": plan["zoom"],
            "music": music.name if music else None, "music_note": note, "encoder": encoder,
            "shown": [[Path(m["path"]).stem, w0, w1]]}


WORDS_PER_BEAT = 7          # the voice says about 4 words a second, so a beat of about 7 words lasts 1.5-2 s


def layout(line, n, by_id):
    """A narrated line's beats: a line about the stars floats the footage as one 16:9 card (the HUD stays in view);
    the others switch between full screen and a card about every WORDS_PER_BEAT words (the explainer's 1.5-2 s), at
    least twice, starting with a different one on every other line, so the picture keeps changing."""
    stars = any(by_id.get(i, {}).get("kind") == "stars" for i in line.get("evidence", []))
    if stars:
        return ["card"]
    count = max(2, math.ceil(len(str(line.get("say", "")).split()) / WORDS_PER_BEAT))
    kinds = ("full", "card") if n % 2 == 0 else ("card", "full")
    return [kinds[k % 2] for k in range(count)]


def narrated_script(short, m, ats=None):
    """The explainer.py script for a narrated Short: the voice (am_puck), captions, music, sound effects and the
    subscribe pill as every Short, over the moment's footage playing on in real time: each beat starts where the one
    before it ended. ats: the start of every beat, per line; None puts every beat at the window's start (only for
    timing, which doesn't depend on it)."""
    by_id = {e["id"]: e for e in m["evidence"]}
    lines = []
    for n, line in enumerate(short["lines"]):
        beats = []
        for k, kind in enumerate(layout(line, n, by_id)):
            at = ats[n][k] if ats else m["window"][0]
            beat = {"beat": kind, "file": m["path"], "at": f"{at:.3f}", "sound": True}
            if kind == "card":
                beat["card_aspect"] = "16:9"
            beats.append(beat)
        out = {"say": line["say"], "beats": beats}
        for key in ("green", "red"):
            if isinstance(line.get(key), list):
                out[key] = line[key]
        lines.append(out)
    speed = short.get("speed", 1.0)
    return {"title": short["title"], "voice": "am_puck", "speed": speed if isinstance(speed, (int, float)) else 1.0,
            "music": "auto", "sfx": True, "end_pill": END_PILL, "lines": lines}


def render_narrated(short, m, out_dir, facts, today, indexes, log=print, timing=None, render=None):
    """A NARRATED Short: time the voice, put each beat's footage where the one before ended, check every line
    against the evidence on screen while it is spoken, render with explainer.py, then check what got on screen."""
    import explainer
    w0, w1 = m["window"]
    path = out_dir / "script_timing.json"
    path.write_text(json.dumps(narrated_script(short, m), indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        times = (timing or explainer.timing)(path, GTA_ROOT)
    except explainer.ScriptError as e:
        raise RenderProblem(f"explainer.py can't read the script: {e}") from None
    finally:
        path.unlink(missing_ok=True)
    total = times["total_s"]
    words = footage_short.make_short.spoken_words({"lines": short["lines"]})
    problems = []
    room = w1 - w0
    top = min(NARRATED_LENGTH[1], room)
    if not NARRATED_LENGTH[0] <= total <= top:
        rate = words / max(total, 1.0)
        change = round((min(max(total, NARRATED_LENGTH[0] + 2), top - 1) - total) * rate)
        problems.append(f"the voice comes to {total:.1f} s with {words} words; this Short can be {NARRATED_LENGTH[0]:g}-"
                        f"{top:.0f} s (its clean footage is {room:.0f} s). {'Add' if change > 0 else 'Cut'} about "
                        f"{abs(change)} words")
    ats, spans, t = [], [], w0
    for n, line_lengths in enumerate(times["lines"]):
        ats.append([])
        start = t
        for seconds in line_lengths:
            ats[-1].append(round(t, 3))
            t += seconds
        spans.append((round(start, 2), round(t, 2)))
    more, flags, claims = truth.check_short(short, m, facts, today, spans)
    problems += more
    if problems:
        raise RenderProblem("\n  ".join(problems))
    script = narrated_script(short, m, ats)
    resolved = out_dir / "script_resolved.json"
    resolved.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    if render:
        render(resolved, out_dir)
    else:
        try:
            explainer.render(resolved, GTA_ROOT, SHORTS_DIR, run_dir=out_dir)
        except explainer.ScriptError as e:
            raise RenderProblem(f"explainer.py can't render it: {e}") from None
    run_log = json.loads((out_dir / "log.json").read_text(encoding="utf-8"))
    shown = footage_short.shown_spans(run_log, [m["path"]])
    bad = footage_short.unclean(shown, indexes)
    if bad:
        raise RenderProblem("what got on screen is not all clean footage:\n  " + "\n  ".join(bad))
    (out_dir / "claims.txt").write_text(truth.claims_text(short, m, claims, facts), encoding="utf-8")
    return {"length_s": round((run_log.get("durations") or {}).get("video_s", total), 2), "flags": flags,
            "line_spans": spans, "music": Path((run_log.get("music") or {}).get("file") or "").name or None,
            "shown": [[Path(s["video"]).stem, s["start_s"], s["end_s"]] for s in shown]}


# --- Engine v2 (PR 26): the story layer ---

STORY_LENGTH = (25.0, 40.0)              # a narrated story Short's length (the formats' 25-40 s)
ENGINES = ("v4", "v3", "v2", "v1")             # v3 (launch-fixes, default): the Step 0 sample's way, both types;
                                         # v2: PR 26's story layer (narrated only); v1: PR 25's explainer.py render
# Words that never anchor a line to a 'seen' item, however often the model's own lines use them.
ANCHOR_STOP = {"with", "from", "into", "that", "this", "there", "their", "they", "then", "than", "what", "when",
               "where", "while", "your", "have", "been", "just", "like", "over", "onto", "through", "right", "behind",
               "front", "some", "also", "very", "still", "here", "game", "gta", "screen", "shows", "seen", "view",
               "camera", "player", "character", "scene", "image", "frame", "appears", "visible", "near", "next"}


def compare_of(m, short):
    """The F1 other side this pick offers, or None. Only an F1 Short uses it: a pick that could also be F1 but is made
    as F3 shows none of it, so it gets no stand-in flag and its GTA 5 clip is not logged as shown."""
    if short.get("format") != "F1":
        return None
    return next((f["compare"] for f in m.get("format_options") or [] if f["format"] == "F1"), None)


def line_anchors(line, by_id):
    """The words of an on_screen line that name one of its own footage evidence items, each with that item's time:
    edit2 puts the item on screen within 0.5 s of the word (the owner's rule 4). Star, police, crash ... words go by
    truth.WORDS; other words by the item's own words ('seen' lines), leaving out ANCHOR_STOP."""
    if line.get("claim") != "on_screen":
        return []
    items = [by_id[i] for i in line.get("evidence", []) if i in by_id and by_id[i]["kind"] not in ("rockstar", "compare")]
    out, used = [], set()
    for w in truth.words_of(line.get("say", "")):
        low = w.lower().strip("'’")
        family = next((tuple(words) for words, _ in truth.WORDS if low in words), None)
        if low in used or low in ANCHOR_STOP or (family and family in used):
            continue                         # one anchor per thing a line names ("wanted ... stars" is one thing)
        named = [e for e in items if truth.names_item(low, e)]
        if named:                            # the item of the word's own kind first ("police" -> police seen), then the
            e = min(named, key=lambda e: (anchor_rank(low, e), e["to_s"] - e["from_s"]))   # most exact time
            out.append({"word": w, "from_s": e["from_s"], "to_s": e["to_s"], "evidence": e["id"]})
            used.update({low, family} - {None})
    return out


def anchor_rank(word, item):
    """0 when the item is of the kind a word's family is mainly about (a star word and a HUD star item, a police word
    and a police item), else 1."""
    for words, kinds in truth.WORDS:
        if word in words:
            kind, value = kinds[0]
            return 0 if item["kind"] == kind and (value is None or item["value"] == value) else 1
    return 0


def story_script(short, m, folder, dry_run):
    """The edit2 script for a narrated story Short: the moment's window as one continuous event, each line's anchors,
    its pops (an F1 pip or split gets the other side's file, label and the DRYRUN STAND-IN note), the break, the
    yellow captions, am_puck and the silent subscribe pill."""
    by_id = {e["id"]: e for e in m["evidence"]}
    side = compare_of(m, short)
    profile = Path(folder) / "profiles" / f"{m['profile']}.json"
    lines = []
    for line in short["lines"]:
        pops = []
        for p in line.get("pops", []) or []:
            p = {k: v for k, v in p.items() if k not in ("compare",)}
            if p.get("kind") == "pip" and line_compare(line) and side:
                p.update(file=side["file"], at=side["at"], label=side["label"])
                if side.get("note"):
                    p["note"] = side["note"]
            pops.append(p)
        lines.append({"say": line["say"], "anchors": line_anchors(line, by_id), "pops": pops,
                      "payoff": bool(line.get("payoff")), "loop": bool(line.get("loop"))})
    script = {"title": short["title"], "voice": "am_puck", "caption_style": short.get("caption_style", "yellow"),
              "music": "auto", "game_audio": True, "end_pill": END_PILL,
              "profile": str(profile if profile.is_file() else m["profile"]),
              "moment": {"file": m["path"], "window": list(m["window"]), "focus": short.get("focus", 0.5),
                         "action_s": m.get("anchor_s")}, "lines": lines}
    brk = short.get("break")
    if isinstance(brk, dict):
        brk = {k: v for k, v in brk.items() if k not in ("compare", "evidence")}
        if brk.get("kind") == "split" and side:
            own = "GTA 5" if side["label"] == "GTA 6" else "GTA 6"
            brk.update(file=side["file"], at=side["at"], label=brk.get("label") or own,
                       bottom_label=brk.get("bottom_label") or side["label"])
            if side.get("note"):
                brk["note"] = side["note"]
        if brk.get("kind", "freeze") == "freeze" and short["break"].get("evidence") in by_id and "at_s" not in brk:
            e = by_id[short["break"]["evidence"]]       # the first moment the detail is on screen
            brk["at_s"] = e.get("at_s", min(e["from_s"] + 0.5, e["to_s"]))
        script["break"] = brk
    return script


def line_compare(line):
    return any(str(i).startswith(("R", "C")) for i in line.get("evidence", []))


def story_spans(tl, m, side):
    """What a v2 render puts on screen, for the clean-footage check and the used-footage log: every footage shot,
    the loop ending and the freeze's frame from the moment, and an F1 clip from the GTA 5 library."""
    stem = Path(m["path"]).stem
    out = []
    for s in tl["shots"]:
        end = s["src"] + (s["frames"] / 30.0 if s["kind"] != "break" else 0.04)
        what = {"footage": "a background shot", "loop": "the loop ending", "break": "the break"}[s["kind"]]
        out.append({"video": m["path"], "stem": stem, "start_s": round(s["src"], 3), "end_s": round(end, 3),
                    "line": None, "what": what})
    if side and not side.get("standin"):
        out.append({"video": side["file"], "stem": Path(side["file"]).stem, "start_s": side["at"],
                    "end_s": side["at"] + 6.0, "line": None, "what": "the GTA 5 side"})
    return out


def render_story(short, m, out_dir, facts, today, indexes, folder, dry_run, log=print, synth=None, v2_render=None):
    """A NARRATED story Short through edit2 (engine v2): the script is checked (truth.py, the story rules), the voice
    is made and the timeline planned (one moment in order, every named thing on screen within 0.5 s of its word, the
    setup first), then the timing rules (25-40 s, the payoff at 45-75 %), then it renders and what got on screen is
    checked against the index."""
    import edit2
    side = compare_of(m, short)
    if side and side.get("standin") and not dry_run:
        raise RenderProblem("a DRYRUN STAND-IN is never used outside a dry run")
    script = story_script(short, m, folder, dry_run)
    try:
        loaded = edit2.load(script)
        tl = edit2.plan(loaded, synth)
    except (edit2.EditError, explainer_error()) as e:
        raise RenderProblem(f"edit2 can't make it: {e}") from None
    problems = list(tl["problems"])
    total = tl["total_s"]
    if not STORY_LENGTH[0] <= total <= STORY_LENGTH[1]:
        words = sum(len(edit2.line_tokens(l["say"])) for l in short["lines"])
        problems.append(f"the Short comes to {total:.1f} s with {words} words; a story Short is {STORY_LENGTH[0]:g}-"
                        f"{STORY_LENGTH[1]:g} s ({'add' if total < STORY_LENGTH[0] else 'cut'} words)")
    payoff = next((k for k, l in enumerate(short["lines"]) if l.get("payoff")), None)
    payoff_at = None
    if payoff is not None:
        payoff_at = round(tl["line_starts"][payoff] / total, 3)
        if not truth.PAYOFF_AT[0] <= payoff_at <= truth.PAYOFF_AT[1]:
            problems.append(f"line {payoff + 1}: the payoff starts {payoff_at:.0%} into the Short; it belongs at "
                            f"{truth.PAYOFF_AT[0]:.0%}-{truth.PAYOFF_AT[1]:.0%} (move words before or after it)")
    more, flags, claims = truth.check_short(short, m, facts, today, tl["line_spans"])
    problems += more
    if problems:
        raise RenderProblem("\n  ".join(dict.fromkeys(problems)))
    (out_dir / "script_v2.json").write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        data = (v2_render or edit2.render_plan)(loaded, tl, out_dir, log=lambda s: log("  " + s))
    except edit2.EditError as e:
        raise RenderProblem(f"edit2 can't render it: {e}") from None
    shown = story_spans(tl, m, side)
    bad = footage_short.unclean(shown, indexes)
    if bad:
        raise RenderProblem("what got on screen is not all clean footage:\n  " + "\n  ".join(bad))
    if side and side.get("standin"):
        flags.append(f"DRYRUN STAND-IN: the GTA 6 side of this F1 is Rockstar's official picture "
                     f"({Path(side['file']).name}), only for the dry run")
    skipped = [p for p in data.get("pops", []) if p.get("skipped")]
    if skipped:
        flags.append("CIRCLE LEFT OUT: " + "; ".join(f"line {p['line']} on {p['word']!r}" for p in skipped)
                     + " (its target couldn't be followed)")
    gaps = data.get("gaps_over_2s") or []
    (out_dir / "claims.txt").write_text(truth.claims_text(short, m, claims, facts), encoding="utf-8")
    return {"length_s": round(data.get("length_s", total), 2), "flags": flags, "format": short.get("format"),
            "payoff_at": payoff_at, "line_spans": tl["line_spans"], "music": Path((data.get("music") or {}).get("file")
                                                                                   or "").name or None,
            "anchors": [{k: x[k] for k in ("line", "word", "shown_s", "from_s", "to_s", "ok")} for x in tl["anchors"]],
            "gaps_over_2s": gaps, "pops": len(data.get("pops", [])) - len(skipped),
            "pop_interval_s": data.get("pop_interval_s"), "background_avg_s": (data.get("background") or {}).get("average_s"),
            "shown": [[s["stem"], s["start_s"], s["end_s"]] for s in shown]}


# --- Engine v3 (launch-fixes): every Short the way the Step 0 sample was made, with the owner's visual rules ---
# The owner's rule after launch night: no Short ever ships as raw gameplay. Both types get a hook card from frame 0,
# the am_puck voice with yellow word captions, music and sound effects, a setup -> payoff and a loop; an ACTION Short
# has a short voice and the game's sound in.
# The owner's rules after watching the posted sample (Nov 19), checked here in code:
#   1. full-screen gameplay: every beat is a 9:16 window of the recording that follows the action (proof.py); a HUD
#      close-up is only an inset on top, for at most 2 s. No card or compare (a small box over a blurred background).
#   2. caption = picture: after the render, every caption word that names a HUD thing, a count, "gone", a star's look
#      or something to see (police, a car, a street ...) is checked against the frame on screen at that moment (the
#      HUD reader, and the vision model); one that disagrees fails the Short.
#   3. B-roll is bright GTA 6: a beat that is too dark, mostly blank or only the character's back is rejected.
#   4. safe zone: insets inside YouTube's safe area; a beat with game text under the title at the bottom is rejected.
#   5. zoom cap: an inset zooms at most proof.ZOOM_CAP; a smaller detail gets a wider crop and a red box.
ENGINE_V3_BEATS = ("full", "banner")
TOPIC_LENGTH = (25.0, 39.0)              # a narrated (topic) Short's length
ACTION_V3_LENGTH = (16.0, 32.0)          # an action Short's
OPEN_CARD_CHARS = 13                     # explainer's open card: characters a line
PAYOFF_V3 = (0.45, 0.85)                 # the payoff line starts this far in (the owner's sample: about 80 %)
BEAT_NEAR_S = 60.0                       # a beat of the pick's own recording stays this near its window
WORD_LOOK_S = 0.15                       # a caption is checked at its word's start plus this
GONE_BACK_S = 15.0                       # "gone": something must have been there in the 15 s before
HUD_NOUNS = {"star": "stars", "stars": "stars", "wanted": "stars", "icon": "icons", "icons": "icons",
             "heat": "icons"}
GONE_WORDS = {"gone", "disappear", "disappears", "disappeared", "vanish", "vanishes", "vanished", "cleared", "none",
              "empty"}
ALL_WORDS = {"last", "all", "every", "no", "none"}
LOOK_WORDS = {"hollow": ("red outline", "white outline"), "outline": ("red outline", "white outline"),
              "outlined": ("red outline", "white outline"), "red": ("red", "red outline"),
              "white": ("white", "white outline"), "solid": ("red", "white")}
SEEN_WORDS = {"police": "police", "cop": "police", "cops": "police", "officer": "police", "officers": "police",
              "cruiser": "police", "car": "vehicle", "cars": "vehicle", "truck": "vehicle", "trucks": "vehicle",
              "van": "vehicle", "suv": "vehicle", "bike": "vehicle", "motorbike": "vehicle", "boat": "vehicle",
              "boats": "vehicle", "helicopter": "vehicle", "vehicle": "vehicle", "vehicles": "vehicle",
              "crash": "crash", "crashes": "crash", "crashed": "crash", "wreck": "crash", "explosion": "explosion",
              "explodes": "explosion", "exploded": "explosion", "fireball": "explosion", "shooting": "shooting",
              "shoot": "shooting", "shoots": "shooting", "shootout": "shooting", "beach": "water", "ocean": "water",
              "sea": "water", "water": "water", "river": "water", "street": "street", "streets": "street",
              "road": "street", "traffic": "street", "highway": "street", "people": "people", "crowd": "people",
              "pedestrians": "people", "night": "night", "skyline": "buildings", "city": "buildings",
              "buildings": "buildings"}


def open_card_text(hook, chars=OPEN_CARD_CHARS):
    """The hook as explainer's open card: upper case, 2-3 lines of at most `chars`, or None if it can't fit. Engine v4's
    smaller card takes longer lines (assemble.CARD_CHARS), so it needs fewer of them in its band."""
    words, lines = str(hook).upper().split(), [""]
    for w in words:
        if len(w) > chars:
            return None
        if lines[-1] and len(lines[-1]) + 1 + len(w) > chars:
            lines.append(w)
        else:
            lines[-1] = (lines[-1] + " " + w).strip()
    return "|".join(lines) if 1 <= len(lines) <= 3 and lines[0] else None


def auto_beats(line, by_id, n, action):
    """Beats for a line that names none: full-screen footage at each cited evidence item (time order), with the stars
    as an inset for a star, heat or star-look item (the icon row boxed for heat); at least 2 for a line of 7 words or
    more."""
    items = sorted((by_id[i] for i in line.get("evidence", []) if i in by_id), key=lambda e: e["from_s"])
    beats = []
    for e in items:
        at = e.get("at_s", e["from_s"])
        if e["kind"] in ("stars", "heat", "star_look"):
            beats.append({"beat": "full", "at": at,
                          "inset": {"zoom": "stars", "box": "icons" if e["kind"] == "heat" else "stars"}})
        else:
            beats.append({"beat": "full", "at": at + 0.5})
    want = 2 if len(str(line.get("say", "")).split()) >= 7 else 1
    while beats and len(beats) < want:
        beats.append({"beat": "full", "at": beats[-1]["at"] + 2.0})
    return beats


def beat_key(spec):
    import hashlib
    return hashlib.sha1(json.dumps(spec, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def compose_beat(video, at, out_dir, prof, frame, inset=None, focus=None, sound=False, info=None):
    """One beat's 1080x1920 clip (proof.CLIP_S long, reused when the same beat was made before): the recording from
    `at`, full screen, the 9:16 window following the action (or held at `focus`, 0-1), with the inset on top. Returns
    (clip path, its plan: the window's keys and the inset's rect, box and times)."""
    import proof
    info = info or clip.probe(shutil.which("ffprobe"), Path(video))
    shown_w, shown_h = proof.shown_size(info)
    cw = proof.crop_width(shown_h)
    ins = None
    if inset:
        rect, detail = proof.inset_rect(inset.get("zoom", "stars"), prof, frame)
        box = inset.get("box")
        if isinstance(box, str):
            box = proof.hud_box(box, prof)
        elif box is None and detail is not None:
            box = detail
        t0 = float(inset.get("start", proof.INSET_START_S))
        ins = {"rect": rect, "zoom": inset.get("zoom", "stars"), "t0": t0,
               "t1": round(t0 + float(inset.get("length", proof.INSET_LEN_S)), 3),
               "from_s": float(inset.get("at", at + t0)), "box": proof.box_in_inset(box, rect) if box else None,
               "box_name": inset.get("box") if isinstance(inset.get("box"), str) else ("detail" if box else None)}
    spec = {"video": str(video), "at": round(at, 3), "inset": ins, "focus": focus, "sound": bool(sound),
            "clip_s": proof.CLIP_S, "v": 1}
    path = Path(out_dir) / "beats" / f"{beat_key(spec)}.mp4"
    plan_file = path.with_suffix(".json")
    if path.is_file() and plan_file.is_file():
        return path, json.loads(plan_file.read_text(encoding="utf-8"))
    if focus is not None:
        keys = [(0.0, min(max(0, round(focus * shown_w - cw / 2)), shown_w - cw))]
    else:
        frames, fw, fh = proof.gray_frames(video, at, proof.CLIP_S, proof.FOLLOW_FPS, proof.FOLLOW_W, info)
        keys = proof.crop_keys(proof.follow_track(frames, fw, fh, cw / shown_w), proof.FOLLOW_FPS, shown_w, cw)
    proof.vertical_clip(video, at, proof.CLIP_S, path, keys, ins, sound, info)
    plan = {"keys": keys, "crop_w": cw, "shown": [shown_w, shown_h], "inset": ins}
    plan_file.write_text(json.dumps(plan), encoding="utf-8")
    return path, plan


def plan_v3(short, m, videos=None):
    """(plan, problems): every beat of every line as a spec, checked, nothing made yet. plan: per line, per beat
    {"kind", "video", "path", "from_s", "own", "inset" (as written, or None), "focus", "sound", "label", "text",
    "weight"}. videos: {recording stem: path} for B-roll from another recording ("video")."""
    import proof
    action = m["type"] == "action"
    by_id = {e["id"]: e for e in m["evidence"]}
    problems, plan = [], []
    w0, w1 = m["window"]
    videos = dict(videos or {}, **{m["video"]: m["path"]})
    for n, line in enumerate(short.get("lines") or [], 1):
        raw = line.get("beats") or auto_beats(line, by_id, n, action)
        lp = []
        for k, b in enumerate(raw, 1):
            where = f"line {n}, beat {k}"
            kind = b.get("beat", "full") if isinstance(b, dict) else None
            if kind in ("card", "compare"):
                problems.append(f"{where}: a {kind} puts the gameplay in a small box over a blurred background; use a "
                                "full beat (a HUD close-up goes in its \"inset\", for at most 2 s)")
                continue
            if kind not in ENGINE_V3_BEATS:
                problems.append(f"{where}: \"beat\" is one of {', '.join(ENGINE_V3_BEATS)}")
                continue
            at = b.get("at")
            if isinstance(at, bool) or not isinstance(at, (int, float)):
                problems.append(f"{where}: \"at\" is the second of the recording it shows")
                continue
            stem = b.get("video") or m["video"]
            if stem not in videos:
                problems.append(f"{where}: no recording {stem!r} in the library")
                continue
            own = stem == m["video"]
            if own and not w0 - BEAT_NEAR_S <= at <= w1 + BEAT_NEAR_S:
                problems.append(f"{where}: at {at:.1f} s is far from this pick's window ({w0:.0f}-{w1:.0f} s); "
                                "B-roll from elsewhere names its \"video\"")
                continue
            inset = b.get("inset")
            if inset is not None:
                if not isinstance(inset, dict):
                    problems.append(f"{where}: \"inset\" is {{\"zoom\": \"stars\", \"box\": \"icons\"}} or similar")
                    continue
                if not own:
                    problems.append(f"{where}: an inset shows the pick's own HUD; it can't go on B-roll from "
                                    "another recording")
                    continue
                length, start = inset.get("length"), inset.get("start")
                if length is not None and (isinstance(length, bool) or not isinstance(length, (int, float))
                                           or not 0.5 <= length <= proof.INSET_MAX_S):
                    problems.append(f"{where}: an inset shows for 0.5-{proof.INSET_MAX_S:g} s (\"length\")")
                    continue
                if start is not None and (isinstance(start, bool) or not isinstance(start, (int, float))
                                          or start < 0):
                    problems.append(f"{where}: an inset's \"start\" is the seconds into its beat it appears")
                    continue
            focus = b.get("focus")
            if focus is not None and (isinstance(focus, bool) or not isinstance(focus, (int, float))
                                      or not 0 <= focus <= 1):
                problems.append(f"{where}: \"focus\" is 0-1 (where across the picture the window sits)")
                continue
            if kind == "banner" and not str(b.get("text") or "").strip():
                problems.append(f"{where}: a banner has \"text\" (1-3 words)")
                continue
            lp.append({"kind": kind, "video": stem, "path": videos[stem], "from_s": round(at, 2), "own": own,
                       "inset": dict(inset) if inset else None, "focus": focus, "sound": bool(b.get("sound", action)),
                       "label": b.get("label"), "text": b.get("text"), "weight": b.get("weight")})
        plan.append(lp)
    return plan, problems


def v3_script(short, plan, files=None):
    """The explainer script for a plan. files: per line, per beat, the composed clip (played from its start); without
    them every beat points at its recording at its second (enough for explainer.timing)."""
    lines = []
    for n, (line, lp) in enumerate(zip(short.get("lines") or [], plan)):
        beats = []
        for k, e in enumerate(lp):
            clip_file = files[n][k] if files else None
            out = {"beat": e["kind"], "file": str(clip_file or e["path"]), "at": "0" if clip_file else f"{e['from_s']:.3f}"}
            if e["sound"]:
                out["sound"] = True
            if e["kind"] == "banner":
                out["text"] = str(e["text"])
            elif e.get("label"):
                out["label"] = e["label"]
            if e.get("weight"):
                out["weight"] = e["weight"]
            beats.append(out)
        out_line = {"say": line.get("say", ""), "beats": beats}
        for key in ("green", "red"):
            if isinstance(line.get(key), list):
                out_line[key] = line[key]
        lines.append(out_line)
    hooks = short.get("hooks") or []
    star = short.get("star")
    hook = hooks[star] if isinstance(star, int) and 0 <= star < len(hooks) else None
    script = {"title": short.get("title") or "Short", "voice": "am_puck",
              "speed": short.get("speed", 1.0) if isinstance(short.get("speed"), (int, float)) else 1.0,
              "music": "auto", "sfx": True, "end_pill": END_PILL, "lines": lines}
    card = short.get("open_card") or (open_card_text(hook) if hook else None)
    if card:
        script["open_card"] = card
    return script


def beat_slots(times):
    """Per line, per beat: (start on the Short's timeline, seconds on screen), from explainer.timing."""
    out, t = [], 0.0
    for lengths in times["lines"]:
        row = []
        for seconds in lengths:
            row.append((round(t, 3), seconds))
            t += seconds
        out.append(row)
    return out


def hud_words(words, line_say):
    """The indexes of the spoken words that need the stars' inset on screen: a HUD word (star, icon, heat, wanted),
    "gone" and the like, a number just before a HUD word, and a star's look in a line about stars."""
    toks = word_tokens(line_say)
    stars_line = any(x in ("star", "stars") for x in toks)
    out = []
    for k, w in enumerate(words):
        word = (word_tokens(w["text"]) or [""])[0]
        nxt = (word_tokens(words[k + 1]["text"]) or [""])[0] if k + 1 < len(words) else ""
        nxt2 = (word_tokens(words[k + 2]["text"]) or [""])[0] if k + 2 < len(words) else ""
        number = word.isdigit() or (word in truth.NUMBER_WORDS and word != "no")
        if (word in HUD_NOUNS or word in GONE_WORDS or (word == "no" and nxt in HUD_NOUNS)
                or (number and (nxt in HUD_NOUNS or nxt2 in HUD_NOUNS)) or (word in LOOK_WORDS and stars_line)):
            out.append(k)
    return out


def place_insets(short, plan, times):
    """Engine v3: each inset is timed to the words that name what it shows: from just before the first HUD word of
    its beat to just after the last, at most proof.INSET_MAX_S (an inset written with "start" keeps it). A line that
    names the HUD on a beat of its own footage with no inset gets one there (the stars, the icon row boxed for icons
    or heat). Returns notes for the log."""
    import proof
    words = times.get("words") or []
    slots = beat_slots(times)
    notes = []
    line_ends = [row[-1][0] + row[-1][1] if row else 0.0 for row in slots]
    for n, (line, lp) in enumerate(zip(short.get("lines") or [], plan)):
        lo = slots[n][0][0] if slots[n] else 0.0
        hi = line_ends[n]
        mine = [w for w in words if lo <= w["start_s"] < hi]
        need = [mine[k] for k in hud_words(mine, line.get("say", ""))]
        for k, (e, (start, seconds)) in enumerate(zip(lp, slots[n])):
            inside = [w for w in need if start <= w["start_s"] < start + seconds]
            ins = e.get("inset")
            if ins is None and inside and e["own"]:
                toks = word_tokens(line.get("say", ""))
                ins = e["inset"] = {"zoom": "stars",
                                    "box": "icons" if any(x in ("icon", "icons", "heat") for x in toks) else "stars"}
                notes.append(f"line {n + 1}, beat {k + 1}: added the stars' inset for "
                             + ", ".join(f'"{w["text"]}"' for w in inside))
            if ins is None:
                continue
            if ins.get("start") is None:
                if inside:
                    t0 = max(0.0, inside[0]["start_s"] - start - 0.25)
                    t1 = min(seconds, max(t0 + 0.8, inside[-1]["end_s"] - start + 0.35))
                else:
                    t0, t1 = proof.INSET_START_S, min(seconds, proof.INSET_START_S + proof.INSET_LEN_S)
                ins["start"], ins["length"] = round(t0, 2), round(max(0.5, min(proof.INSET_MAX_S, t1 - t0)), 2)
            elif ins.get("length") is None:
                ins["length"] = proof.INSET_LEN_S
    return notes


def compose_plan(plan, out_dir, prof, frame, log=print):
    """Every beat's full-screen clip (compose_beat); fills each plan entry's "clip" and "inset" (as made). Returns
    (files per line per beat, problems)."""
    import proof
    files, problems = [], []
    for n, lp in enumerate(plan, 1):
        row = []
        for k, e in enumerate(lp, 1):
            try:
                path, cplan = compose_beat(e["path"], e["from_s"], out_dir, prof, frame, e.get("inset"), e["focus"],
                                           e["sound"])
            except (proof.ProofError, clip.ProfileError) as err:
                problems.append(f"line {n}, beat {k}: {err}")
                row.append(None)
                continue
            e["clip"], e["inset"] = str(path), cplan.get("inset")
            if cplan.get("inset"):
                problems += proof.safe_zone_problems(proof.inset_screen_rect(), f"line {n}, beat {k}: the inset")
            row.append(path)
        files.append(row)
    return files, problems


def v3_spans(plan, times):
    """Per line, the pick's own recording seconds on screen while it is spoken (B-roll from another recording left
    out), and every beat: ([(from_s, to_s) or None per line], [(line, video, from_s, to_s, seconds) per beat])."""
    line_spans, beats = [], []
    for n, (lp, lengths) in enumerate(zip(plan, times["lines"]), 1):
        own = []
        for entry, seconds in zip(lp, lengths):
            span = (n, entry["video"], entry["from_s"], round(entry["from_s"] + seconds, 2), seconds)
            beats.append(span)
            if entry["own"]:
                own.append(span)
        line_spans.append((min(s[2] for s in own), max(s[3] for s in own)) if own else None)
    return line_spans, beats


def frame_jpeg(clip_file, times, out, width=360, height=640, hide=None):
    """A JPEG of a clip's frames at times, side by side (what the frame question looks at). hide: an [x, y, w, h]
    rect of the 1080x1920 Short blurred out (launch-fixes: the beat question is about the gameplay, and our own
    inset of the HUD's icons made the vision model call every inset beat "game text at the bottom")."""
    import proof
    tiles = len(times)
    inputs, graph = [], []
    for k, t in enumerate(times):
        inputs += ["-ss", f"{max(0.0, t):.3f}", "-i", str(clip_file)]
        if hide:
            sx, sy = width / proof.W, height / proof.H
            x, y = int(hide[0] * sx), int(hide[1] * sy)
            w, h = max(2, int(hide[2] * sx) // 2 * 2), max(2, int(hide[3] * sy) // 2 * 2)
            graph.append(f"[{k}:v]trim=end_frame=1,scale={width}:{height},split[a{k}][b{k}];"
                         f"[b{k}]crop={w}:{h}:{x}:{y},boxblur=20:4[h{k}];[a{k}][h{k}]overlay={x}:{y}[f{k}]")
        else:
            graph.append(f"[{k}:v]trim=end_frame=1,scale={width}:{height}[f{k}]")
    stack = "".join(f"[f{k}]" for k in range(tiles))
    graph.append(f"{stack}hstack=inputs={tiles}[v]" if tiles > 1 else f"{stack}null[v]")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(graph), "-map", "[v]",
                    "-frames:v", "1", "-q:v", "3", str(out)], capture_output=True)
    return Path(out) if Path(out).is_file() else None


def beat_checks(plan, times, out_dir, checker, log=print, what="line", max_s=None):
    """Rules 3 and 4 for every beat, on the part of its clip that gets on screen: (problems, rejected shots,
    flags). Measured: too dark or mostly blank (proof.shot_problems on 3 frames, the inset left out). Asked (the
    vision model, frame_v1, 2 frames): too dark, empty, only the character's back, game text at the bottom (under
    YouTube's title); text on the right edge is only a flag. Engine v4 passes its shots as one-beat "lines"
    (what="shot") with no length limit (max_s=0)."""
    import proof
    problems, rejected, flags = [], [], []
    max_s = proof.CLIP_S - 0.3 if max_s is None else max_s
    for n, (lp, lengths) in enumerate(zip(plan, times["lines"]), 1):
        for k, (entry, seconds) in enumerate(zip(lp, lengths), 1):
            if seconds <= 0:
                continue
            where = (f"{what} {n}" + (f", beat {k}" if what == "line" else "")
                     + f" ({entry['video'][-6:]} at {hms(entry['from_s'])})")
            if max_s and seconds > max_s:
                problems.append(f"{where} is on screen {seconds:.1f} s; split it into two beats (a clip is "
                                f"{proof.CLIP_S:g} s)")
                continue
            ts = [seconds * f for f in (0.2, 0.5, 0.8)]
            why = []
            tag = f"l{n}b{k}" if what == "line" else f"s{n}"
            ins = entry.get("inset")
            sk = None
            if ins:
                x, y, w, h = proof.inset_screen_rect()
                sk = [x // 4, y // 4, w // 4 + 1, h // 4 + 1]
            found = []
            for t, g in zip(ts, proof.clip_frames(entry["clip"], ts)):
                if len(g) < 270 * 480:
                    continue
                on = bool(ins) and ins["t0"] <= t <= ins["t1"]
                bad = proof.shot_problems(proof.frame_stats(g, 270, 480, sk if on else None))
                if bad:
                    found.append(bad)
            if len(found) >= 2:                  # 2 of its 3 frames: one dark frame (a flash, a cut) is fine
                why += found[-1]
            if checker is not None:
                jpg = frame_jpeg(entry["clip"], [seconds * 0.3, seconds * 0.7], Path(out_dir) / "checks" /
                                 f"{tag}.jpg", hide=proof.inset_screen_rect() if ins else None)
                answer, error = checker.frame(jpg, {"video": entry["video"], "clip": Path(entry["clip"]).name,
                                                    "seconds": round(seconds, 2), "use": "beat"}) if jpg else (None,
                                                                                                             "no frame")
                if answer is None:
                    why.append(f"the vision model couldn't judge it ({error})")
                else:
                    for key, text in (("dark", "too dark to see"), ("empty", "almost nothing to look at"),
                                      ("back_only", "only the character's back in an empty place"),
                                      ("text_bottom", "game text at the bottom, under YouTube's title")):
                        if answer.get(key):
                            why.append(f"{text} (vision model: \"{answer['note']}\")")
                    if answer.get("text_right"):
                        flags.append(f"TEXT ON THE RIGHT: {where} may have game text under YouTube's buttons; look")
            if why:
                why = list(dict.fromkeys(why))
                rejected.append({"line": n, "beat": k, "video": entry["video"], "at": entry["from_s"], "why": why})
                problems.append(f"{where}: " + "; ".join(why) + ". Pick brighter footage (a street, traffic, cars, "
                                "people, the skyline, action)")
    return problems, rejected, flags


def word_tokens(text):
    return [w.lower().strip("'’") for w in re.findall(r"[A-Za-z0-9'’]+", str(text))]


def hud_at(series, t):
    """(stars, look, icons) of the hud6 sample nearest t, or None."""
    if not series:
        return None
    s = min(series, key=lambda s: abs(s[0] - t))
    return s[1:] if abs(s[0] - t) <= 0.6 else None


def caption_checks(run_log, plan, script_lines, checker, hud_series, out_dir, log=print):
    """Rule 2, caption = picture, on the finished render (its log): every word that names a HUD thing must show while
    the stars' inset is on; a count, "gone" or a star's look must be what the HUD reader reads in the inset at that
    moment (the icon row boxed when only the icons are gone); a thing to see (police, a car, a street ...) must be in
    the frame the vision model is shown. Returns the list of checks, each with "ok"."""
    import proof
    by_clip = {}
    for lp in plan:
        for entry in lp:
            by_clip[str(Path(entry["clip"]))] = entry
    visuals = run_log.get("visuals") or []
    words = run_log.get("word_times") or []
    checks = []

    def shown_at(t):
        for v in visuals:
            if v["start_s"] <= t < v["start_s"] + v["seconds"]:
                path = str(Path(str(v["source"]).split("@")[0].split(",")[0]))
                entry = by_clip.get(path)
                return v, entry, (v.get("source_start_s") or 0.0) + (t - v["start_s"])
        return None, None, None
    for k, w in enumerate(words):
        t = w["start_s"] + WORD_LOOK_S
        word = word_tokens(w["text"])
        if not word:
            continue
        word = word[0]
        v, entry, tau = shown_at(t)
        if v is None or entry is None:
            continue
        line = script_lines[v["line"] - 1]["say"] if 0 < v["line"] <= len(script_lines) else ""
        toks = word_tokens(line)
        ins = entry.get("inset")
        inset_on = bool(ins) and ins["t0"] <= tau <= ins["t1"] and ins.get("zoom") == "stars"
        rec_ins = ins["from_s"] + (tau - ins["t0"]) if ins else None
        nxt = word_tokens(words[k + 1]["text"]) if k + 1 < len(words) else []
        prev = [word_tokens(x["text"])[0] for x in words[max(0, k - 2):k] if word_tokens(x["text"])]
        base = {"word": w["text"], "t": round(w["start_s"], 2), "line": v["line"], "video": entry["video"],
                "rec_s": round(entry["from_s"] + tau, 2)}
        if word in HUD_NOUNS:
            ok = inset_on
            checks.append(dict(base, rule="the HUD is on screen", ok=ok,
                               why=None if ok else f"\"{w['text']}\" shows while no close-up of the stars is on screen"))
            if not ok:
                continue
            got = hud_at(hud_series.get(entry["video"]), rec_ins)
            subject = HUD_NOUNS[word]
            number = next((truth.NUMBER_WORDS.get(p, p) for p in reversed(prev) if p.isdigit() or (
                p in truth.NUMBER_WORDS and p != "no")), None)
            if number is not None:
                have = None if got is None else (got[0] if subject == "stars" else got[2])
                ok = have is not None and str(have) == str(number)
                checks.append(dict(base, rule=f"{number} {subject}", ok=ok, saw=have,
                                   why=None if ok else f"\"{number} {subject}\" but the HUD shows {have}"))
        if word in GONE_WORDS or (word == "no" and nxt and nxt[0] in HUD_NOUNS):
            subj = HUD_NOUNS.get(nxt[0]) if word == "no" and nxt else None
            if subj is None:
                before = [HUD_NOUNS[x] for x in toks[:toks.index(word) if word in toks else len(toks)] if x in HUD_NOUNS]
                subj = before[-1] if before else ("icons" if "icon" in " ".join(toks) else "stars")
            if not inset_on:
                checks.append(dict(base, rule=f"{subj} gone", ok=False,
                                   why=f"\"{w['text']}\" shows while no close-up of the {subj} is on screen"))
                continue
            got = hud_at(hud_series.get(entry["video"]), rec_ins)
            have = None if got is None else (got[0] if subj == "stars" else got[2])
            if any(x in ALL_WORDS for x in toks) or subj == "stars":
                ok = have == 0
                rule = f"no {subj} left"
            else:
                earlier = [s for s in hud_series.get(entry["video"]) or [] if rec_ins - GONE_BACK_S <= s[0] < rec_ins]
                most = max(((s[1] if subj == "stars" else s[3]) or 0 for s in earlier), default=0)
                ok = have is not None and have < most
                rule = f"fewer {subj} than before"
            if ok and subj == "icons" and got and got[0] and ins.get("box_name") != "icons":
                ok, why = False, "the icons are gone but the stars are still lit: box the icon row (\"box\": \"icons\")"
            else:
                why = None if ok else f"\"{w['text']}\" but the HUD shows {have} {subj}"
            checks.append(dict(base, rule=rule, ok=ok, saw=have, why=why))
        if word in LOOK_WORDS and any(x in ("star", "stars") for x in toks):
            got = hud_at(hud_series.get(entry["video"]), rec_ins) if inset_on else None
            look = got[1] if got else None
            ok = inset_on and look in LOOK_WORDS[word]
            checks.append(dict(base, rule=f"{word} stars", ok=ok, saw=look,
                               why=None if ok else (f"\"{w['text']}\" but the stars on screen are {look or 'not shown'}")))
        if word in SEEN_WORDS and not (nxt and nxt[0] in ("icon", "icons")):
            field = SEEN_WORDS[word]
            answer, error = (None, "no vision model")
            if checker is not None:
                jpg = frame_jpeg(entry["clip"], [tau], Path(out_dir) / "checks" / f"w{k}_{word}.jpg", 540, 960)
                answer, error = checker.frame(jpg, {"video": entry["video"], "clip": Path(entry["clip"]).name,
                                                    "at": round(tau, 2), "use": "caption"}) if jpg else (None, "no frame")
            ok = bool(answer) and bool(answer.get(field))
            checks.append(dict(base, rule=f"{field} in the frame", ok=ok, saw=(answer or {}).get("note"),
                               why=None if ok else (f"\"{w['text']}\" but the vision model sees no {field} "
                                                    f"(\"{(answer or {}).get('note', error)}\")")))
    return checks


CAPTION_LINE = re.compile(r"Dialogue: [^,]*,[^,]*,[^,]*,Caption,(?:[^,]*,){5}(.*)")   # a word caption in captions.ass
CARD_LINE = re.compile(r"Dialogue: 1,0:00:00\.00,0:00:0([\d.]+),Title,(?:[^,]*,){5}(.*)")     # its open card


def caption_coverage(run_log, plan, script_lines, ass_file, checks):
    """What caption_checks had to look at, so its empty list can't pass for a checked Short (PR 31: M24 showed 23
    captions, none is a word it knows, and nothing said so). Engine v4: (report, bad checks, flags). The captions
    burnt in (captions.ass) must be the lines' words, in order, and each must start over one of the Short's shots. The
    report counts the captions, those over a shot and those tested against their frame; none tested is a flag. A word
    said while the open card shows it must have no caption (PR 33: the same word twice split the eye)."""
    text = Path(ass_file).read_text(encoding="utf-8") if Path(ass_file).is_file() else ""
    burnt = [t for raw in text.splitlines() for m in [CAPTION_LINE.match(raw)] if m
             for t in word_tokens(re.sub(r"\{[^}]*\}", "", m.group(1)))]
    card = CARD_LINE.search(text)
    up = float(card.group(1)) if card else 0.0
    on = set(word_tokens(re.sub(r"\{[^}]*\}|\\N", " ", card.group(2)))) if card else set()
    every = [(t, w) for w in run_log.get("word_times") or [] for t in word_tokens(w["text"])]
    twice = {k for k, (_, w) in enumerate(every) if w["start_s"] < up and set(word_tokens(w["text"])) <= on}
    words = [tw for k, tw in enumerate(every) if k not in twice]
    said = [t for k, t in enumerate(t for ln in script_lines for t in word_tokens(ln["say"])) if k not in twice]
    clips = {str(Path(e["clip"])) for lp in plan for e in lp}
    visuals = run_log.get("visuals") or []

    def shot(t):                                         # (0.01 s: the log rounds its times)
        return next((v for v in visuals if v["start_s"] - 0.01 <= t < v["start_s"] + v["seconds"] + 0.01
                     and str(Path(str(v["source"]))) in clips), None)

    def row(k, rule, why):
        w = words[min(k, len(words) - 1)][1] if words else {"text": "", "start_s": 0.0}
        v = shot(w["start_s"])
        return {"word": w["text"], "t": round(w["start_s"], 2), "line": v["line"] if v else 0, "rule": rule, "ok": False,
                "why": why}
    bad = []
    for k in range(max(len(said), len(burnt))):
        want, got = (seq[k] if k < len(seq) else None for seq in (said, burnt))
        if want != got:                                  # one missing word shifts all the rest: the first one is enough
            bad.append(row(k, "the captions are the words said",
                           f"the caption is {got!r} where the line says {want!r}" if got and want else
                           f"the line's word {want!r} has no caption" if want else f"nobody says the caption {got!r}"))
            break
    lost = [k for k, (_, w) in enumerate(words) if shot(w["start_s"]) is None]
    bad += [row(k, "over a shot", "the caption shows over none of the Short's shots") for k in lost]
    tested = len({(c["word"], c["t"]) for c in checks})
    things = {t for terms in cutplan.SUBJECTS.values() for t in terms} - set(SEEN_WORDS)
    unknown = list(dict.fromkeys(t.upper() for t, _ in every if t in things))
    flags = [] if tested or not burnt else [
        f"CAPTIONS: none of the {len(burnt)} words shown is one the caption check knows, so none was checked against its "
        "frame: look yourself" + (f" ({', '.join(unknown)} names a thing the frame question has no field for)"
                                  if unknown else "")]
    report = {"shown": len(burnt), "over_a_shot": len(words) - len(lost), "tested": tested, "unknown_things": unknown}
    return dict(report, **({"on_card": len(twice)} if twice else {})), bad, flags


def full_screen_share(run_log):
    """The share of the Short whose picture is gameplay full screen (explainer's "full" layout, insets included)."""
    total = ((run_log.get("durations") or {}).get("video_s")) or sum(v["seconds"] for v in run_log.get("visuals") or [])
    full = sum(v["seconds"] for v in run_log.get("visuals") or [] if v.get("layout") == "full")
    return round(min(1.0, full / total), 3) if total else 0.0


def v3_hud(plan, m, prof, hud6=None):
    """{video stem: proof.hud6 series} round every stars inset of the plan (GTA 6 only)."""
    import proof
    spans = {}
    for lp in plan:
        for e in lp:
            ins = e.get("inset")
            if ins and ins.get("zoom") == "stars":
                a, b = ins["from_s"] - GONE_BACK_S - 1, ins["from_s"] + (ins["t1"] - ins["t0"]) + 1
                spans.setdefault(e["video"], []).append((max(0.0, a), b))
    out = {}
    for stem, parts in spans.items():
        path = m["path"] if stem == m["video"] else None
        if path is None:
            continue
        series = []
        for a, b in sorted(parts):
            series += (hud6 or proof.hud6)(path, a, b, prof)
        out[stem] = sorted(set(series))
    return out


class V3Failed(RenderProblem):
    """A rendered v3 Short whose checks failed: the problems, with the checks for the report."""

    def __init__(self, text, checks):
        super().__init__(text)
        self.checks = checks


def render_v3(short, m, out_dir, facts, today, indexes, used=(), log=print, timing=None, render=None, checker=None,
              hud6=None):
    """One Short with engine v3 (both types). Before anything is made: every beat (plan_v3), the voice's timing, the
    lines (truth.check_short on that timing), every beat's footage clean in the index and not shown by an earlier
    Short. Then the insets are timed to their words, the full-screen clips are made, and every beat must be bright,
    full and free of game text under YouTube's title (beat_checks). After the render: every caption against its frame
    (caption_checks). A Short that fails any check is FAILED, with the checks kept for the report."""
    import explainer
    out_dir = Path(out_dir)
    prof = clip.load_profile(m["profile"]) if m.get("profile") else clip.load_profile("gta6")
    frame = tuple(prof.frame)
    videos = {i["video"]["stem"]: i["video"]["path"] for i in indexes or []}
    plan, problems = plan_v3(short, m, videos)
    if problems:
        raise RenderProblem("\n  ".join(dict.fromkeys(problems)))
    path = out_dir / "script_v3.json"
    path.write_text(json.dumps(v3_script(short, plan), indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        times = (timing or explainer.timing)(path, GTA_ROOT)
    except explainer.ScriptError as e:
        raise RenderProblem(f"explainer.py can't read the script: {e}") from None
    total = times["total_s"]
    lo, hi = TOPIC_LENGTH if m["type"] == "narrated" else ACTION_V3_LENGTH
    if not lo <= total <= hi:
        words = footage_short.make_short.spoken_words({"lines": short.get("lines") or []})
        change = round(((lo + hi) / 2 - total) * words / max(total, 1.0))
        problems.append(f"the voice comes to {total:.1f} s with {words} words; a{'n action' if m['type'] == 'action' else ' narrated'} "
                        f"Short is {lo:g}-{hi:g} s: {'add' if change > 0 else 'cut'} about {abs(change)} words")
    line_spans, beats = v3_spans(plan, times)
    pay = [n for n, l in enumerate(short.get("lines") or []) if l.get("payoff")]
    if pay and total > 0:
        at = sum(sum(x) for x in times["lines"][:pay[0]]) / total
        if not PAYOFF_V3[0] <= at <= PAYOFF_V3[1]:
            problems.append(f"the payoff (line {pay[0] + 1}) starts {at:.0%} into the Short; it belongs at "
                            f"{PAYOFF_V3[0]:.0%}-{PAYOFF_V3[1]:.0%}: move lines before or after it")
    paths = dict(videos, **{m["video"]: m["path"]})
    shown = [{"video": paths.get(v, v), "start_s": a, "end_s": b, "line": n, "what": "footage"}
             for n, v, a, b, _ in beats]
    problems += footage_short.unclean(shown, indexes)
    for n, v, a, b, _ in beats:
        if any(u == v and a < e and st < b for u, st, e in used):
            problems.append(f"line {n}: the footage of {v[-6:]} at {hms(a)}-{hms(b)} was shown by an earlier Short: "
                            "move that beat")
    more, flags, claims = truth.check_short(short, m, facts, today, line_spans, engine="v3")
    problems += more
    if problems:
        raise RenderProblem("\n  ".join(dict.fromkeys(problems)))
    if checker is None:
        raise RenderProblem("the vision model isn't running, so the B-roll and caption checks can't run: open Ollama "
                            "and render again")
    for note in place_insets(short, plan, times):
        log("  " + note)
    files, more = compose_plan(plan, out_dir, prof, frame, log)
    if more:
        raise RenderProblem("\n  ".join(dict.fromkeys(more)))
    script = v3_script(short, plan, files)
    more, rejected, beat_flags = beat_checks(plan, times, out_dir, checker, log)
    flags += beat_flags
    checks = {"rejected_shots": rejected, "caption_checks": [], "full_screen": None}
    if more:
        raise V3Failed("\n  ".join(dict.fromkeys(more)), checks)
    resolved = out_dir / "script_resolved.json"
    resolved.write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    path.unlink(missing_ok=True)
    if render:
        render(resolved, out_dir)
    else:
        try:
            explainer.render(resolved, GTA_ROOT, SHORTS_DIR, run_dir=out_dir)
        except explainer.ScriptError as e:
            raise RenderProblem(f"explainer.py can't render it: {e}") from None
    run_log = json.loads((out_dir / "log.json").read_text(encoding="utf-8"))
    checks["full_screen"] = full_screen_share(run_log)
    checks["caption_checks"] = caption_checks(run_log, plan, script["lines"], checker, v3_hud(plan, m, prof, hud6),
                                              out_dir, log)
    (out_dir / "checks.json").write_text(json.dumps(checks, indent=2, ensure_ascii=False), encoding="utf-8")
    bad = [c for c in checks["caption_checks"] if not c["ok"]]
    if bad:
        raise V3Failed("\n  ".join(f"caption \"{c['word']}\" at {c['t']:.1f} s (line {c['line']}): {c['why']}"
                                   for c in bad), checks)
    (out_dir / "claims.txt").write_text(truth.claims_text(short, m, claims, facts), encoding="utf-8")
    sound = any(e["sound"] for lp in plan for e in lp)
    used_spans = sorted({(v, a, b) for _, v, a, b, _ in beats})
    return {"length_s": round((run_log.get("durations") or {}).get("video_s", total), 2), "flags": flags,
            "line_spans": line_spans, "music": Path((run_log.get("music") or {}).get("file") or "").name or None,
            "shown": [list(x) for x in used_spans], "engine": "v3", "game_sound": sound, "checks": checks}


def v4_insets(plan_shots):
    """(insets, problems): each shot's HUD close-up as it will be made, or None. A shot's own "inset": {"zoom":
    "stars" | "tip" | "minimap" | [x, y, w, h], "box": "stars" | "icons", "start": s into the shot, "length": 0.5-2 s}."""
    import proof
    out, problems = [], []
    for s in plan_shots:
        ins = s.get("inset")
        if ins is None:
            out.append(None)
            continue
        where = f"shot {s['n']}: the inset"
        start = ins.get("start", proof.INSET_START_S) if isinstance(ins, dict) else None
        length = ins.get("length", proof.INSET_LEN_S) if isinstance(ins, dict) else None
        seconds = s["frames"] / cutplan.FPS
        if not (cutplan.number(start) and cutplan.number(length)) or start < 0 or start > seconds - 0.5 \
                or not 0.5 <= length <= proof.INSET_MAX_S:
            problems.append(f'{where} is {{"zoom": "stars", "box": "icons", "start": s into the shot, "length": '
                            f'0.5-{proof.INSET_MAX_S:g} s}}, inside the shot')
            out.append(None)
            continue
        t0, t1 = float(start), min(seconds, float(start) + float(length))
        box = ins.get("box")
        out.append({"zoom": ins.get("zoom", "stars"), "box": box, "from_s": round(s["from"] + t0, 3), "t0": t0, "t1": t1,
                    "box_name": box if isinstance(box, str) else None})
    return out, problems


def v4_voice(lines, speed):
    """Every line spoken on its own by am_puck (assemble.speak)."""
    import assemble
    import explainer
    return assemble.speak(explainer.load_kokoro(), lines, "am_puck", speed)


def render_v4(short, m, out_dir, facts, today, indexes, used=(), log=print, checker=None, hud6=None, synth=None,
              make_shot=None, add_inset=None, build=None, loudest=None, cuts=None, clear=None, light=None):
    """One Short with engine v4 (editor-v3, Nov 20): payoff first. Before anything is made: the Short's one payoff
    (the interest gate: none, no Short), its shots as spans of the recording (the payoff whole inside one, no second
    twice, 15 s or more), an action payoff on the loudest moment (the hit), a hook about the HUD shown from frame 0.
    Then the first frame (blurry or blocked moves the start later), the voice and each line placed into the picture
    (ending before its cut; no voice over an action payoff; every cut on a sentence end or an event), the truth rules,
    clean and unused footage. Then the shots (one fixed 9:16 window, steadied only where that measures calmer, HUD
    close-ups on top; a dark shot graded for low light), every shot bright and full, the Short put together
    (assemble.py), every caption against its frame. The makers are replaced in tests."""
    import explainer
    out_dir = Path(out_dir)
    kind = m["type"]
    lines = short.get("lines") or []

    def steady():
        import steady as st
        return st
    loudest = loudest or (lambda v, a, b: steady().loudest(v, a, b))
    cuts = cuts or (lambda v, a, b: steady().scene_cuts(v, a, b))
    clear = clear or (lambda v, a, latest, focus: steady().clear_start(v, a, latest, focus=focus))
    make_shot = make_shot or (lambda v, s, out, grade, log: steady().make_shot(v, s["from"], s["frames"], out,
                                                                                focus=s["focus"], grade=grade, log=log,
                                                                                splits=s.get("splits", ()),
                                                                                speed=s.get("speed", 1.0)))
    light = light or (lambda v, s: steady().light_frames(v, s["from"], s["to"], steady().info_of(v), s["focus"]))
    add_inset = add_inset or (lambda c, v, ins, out, prof: steady().add_inset(c, v, ins, out, steady().info_of(v), prof))
    hud_words = set(HUD_NOUNS)
    w0, w1 = m["window"]
    pay0 = short.get("payoff") if isinstance(short.get("payoff"), dict) else {}
    holder = next((x for x in short.get("shots") or [] if isinstance(x, dict) and cutplan.number(x.get("from"))
                   and cutplan.number(x.get("to")) and cutplan.number(pay0.get("from"))
                   and cutplan.number(pay0.get("to")) and x["from"] <= pay0["from"] and pay0["to"] <= x["to"]), None)
    # the loudest moment of the payoff's own shot (a horn or gunshots elsewhere in the window must not fail it)
    loud = (loudest(m["path"], *((holder["from"], holder["to"]) if holder else (w0, w1)))
            if kind == "action" else None)
    plan, problems, flags = cutplan.plan(short, m, None, loud, (), hud_words)
    if problems:
        raise RenderProblem("\n  ".join(problems))

    s1, pay = plan["shots"][0], plan["payoff"]
    latest = s1["to"] - cutplan.MIN_SHOT_S
    if s1["from"] <= pay["from"] <= s1["to"]:
        latest = min(latest, pay["from"] - cutplan.SETUP_S)
    start, why, tried = clear(m["path"], s1["from"], max(s1["from"], latest), s1["focus"])
    if why:
        raise RenderProblem("\n  ".join(why))
    first_note = None
    if start > s1["from"] + 1e-6:
        first_note = (f"shot 1 starts at {start:.2f} s, not {s1['from']:.2f} s: the frame there was not clear ("
                      + "; ".join(tried[0]["why"]) + ")")
        log("  " + first_note)
        short = dict(short, shots=[dict(short["shots"][0], **{"from": start})] + list(short["shots"][1:]))
        # PR 30: a line that was to start in the seconds now left out (line 1, on frame 0) starts on the new frame 0
        old0 = s1["from"]
        lines = [dict(ln, at=start) if cutplan.number(ln.get("at")) and old0 - 1e-6 <= ln["at"] < start else ln
                 for ln in lines]
        short = dict(short, lines=lines)
        plan, problems, flags = cutplan.plan(short, m, None, loud, (), hud_words)
        if problems:
            raise RenderProblem("\n  ".join(problems))

    speed = short.get("speed", cutplan.SPEED)
    if not cutplan.number(speed) or not 0.8 <= speed <= 1.4:
        raise RenderProblem(f"speed is the voice's speed, 0.8-1.4 (the default is {cutplan.SPEED:g})")
    spoken = (synth or v4_voice)(lines, speed)
    cams = sorted({c for s in plan["shots"] for c in cuts(m["path"], s["from"], s["to"])})
    plan, problems, more = cutplan.plan(short, m, [sp["seconds"] for sp in spoken], loud, cams, hud_words)
    flags = list(dict.fromkeys(flags + more))
    spans = cutplan.line_spans(plan["placed"], len(lines))
    more, tflags, claims = truth.check_short(short, m, facts, today, spans, engine="v4")
    problems += more
    flags += tflags
    shown = [{"video": m["path"], "start_s": s["from"], "end_s": s["to"], "line": s["n"], "what": "footage"}
             for s in plan["shots"]]
    problems += [p.replace("line ", "shot ", 1) for p in footage_short.unclean(shown, indexes)]
    for s in plan["shots"]:
        if any(u == m["video"] and s["from"] < e and st < s["to"] for u, st, e in used):
            problems.append(f"shot {s['n']}: {hms(s['from'])}-{hms(s['to'])} of {m['video'][-6:]} was shown by an "
                            "earlier Short")
    insets, more = v4_insets(plan["shots"])
    problems += more
    if problems:
        raise RenderProblem("\n  ".join(dict.fromkeys(problems)))
    if checker is None:
        raise RenderProblem("the vision model isn't running, so the shot and caption checks can't run: open Ollama "
                            "and render again")

    prof = clip.load_profile(m["profile"]) if m.get("profile") else clip.load_profile("gta6")
    normal = explainer.graded(True)
    (out_dir / "shots").mkdir(parents=True, exist_ok=True)
    report = []
    grade = lit = None
    for s, ins in zip(plan["shots"], insets):
        split = pay["from"] - 0.3                 # steady.PAYOFF_SPLIT_S: the setup and the payoff's jolt apart
        s["splits"] = [split] if s["from"] + 0.5 < split < s["to"] - 0.5 else []
        path = out_dir / "shots" / f"shot{s['n']}.mp4"
        if grade is None or not s.get("replay"):  # the replay shows the payoff's shot again: the same grade
            grade, lit = steady().shot_grade(light(m["path"], s), normal)
        rep = dict(make_shot(m["path"], s, path, grade, log), light=lit)
        if lit["low_light"]:
            flags.append(f"LOW LIGHT: shot {s['n']} is dark (ungraded {lit['level']:.0f} of 255): no contrast or "
                         f"vignette, shadows lifted (gamma {lit['gamma']:.2f}); look that it isn't washed out")
        if ins:
            made = path.with_name(f"shot{s['n']}_inset.mp4")
            add_inset(path, m["path"], ins, made, prof)
            path = made
        s["clip"] = str(path)
        report.append({"shot": s["n"], "from_s": s["from"], "to_s": s["to"], **rep})
        steadied = sum(1 for p in rep.get("pieces", []) if p.get("stabilised"))
        log(f"  shot {s['n']} {hms(s['from'])}-{hms(s['to'])} ({s['frames'] / cutplan.FPS:.1f} s): "
            f"{len(rep.get('pieces', []))} piece(s), {steadied} steadied")
    cplan = [[{"clip": s["clip"], "video": m["video"], "from_s": s["from"], "inset": ins}]
             for s, ins in zip(plan["shots"], insets)]
    times = {"lines": [[s["frames"] / cutplan.FPS] for s in plan["shots"]]}
    more, rejected, bflags = beat_checks(cplan, times, out_dir, checker, log, what="shot", max_s=0)
    flags += bflags
    pay_at = cutplan.to_short(pay["from"], plan["shots"]), cutplan.to_short(pay["to"], plan["shots"])
    replay = plan.get("replay")
    quiet = (pay_at[0], replay["end"]) if replay else pay_at     # no voice over the payoff and its replay: the game's
    if replay:                                                    # own sound comes up for both
        flags.append(f"REPLAY: the impact {hms(replay['from'])}-{hms(replay['to'])} again at {replay['speed']:g}x, "
                     f"{replay['start']:.1f}-{replay['end']:.1f} s into the Short, right after the payoff. No highlight "
                     "ring: nothing tracks the object, so a ring can't be placed reliably")
    checks = {"rejected_shots": rejected, "caption_checks": [], "full_screen": 1.0,
              "payoff": dict(pay, at_s=round(pay_at[0], 2)), "first_frame": tried, "steady": report,
              "replay": replay,
              "lines": plan["placed"], "camera_cuts": cams,
              "cuts": cutplan.cut_reasons(plan["shots"], plan["placed"], cutplan.events_of(m, pay, cams))}
    if more:
        raise V3Failed("\n  ".join(dict.fromkeys(more)), checks)

    import assemble
    hooks, star = short.get("hooks") or [], short.get("star")
    hook = hooks[star] if isinstance(star, int) and 0 <= star < len(hooks) else None
    card = cutplan.card_words(short)              # PR 30: at most 5 words (cutplan.hook_text_problems)
    card = open_card_text(card, assemble.CARD_CHARS) if card else None
    run_log = (build or assemble.build)(out_dir, m["path"], plan["shots"], lines, spoken, plan["placed"], card, kind,
                                        quiet if kind == "action" else None, "auto",
                                        footage_short.make_short.slug_of(short.get("title") or "short"), speed,
                                        "am_puck", log)
    checks["caption_checks"] = caption_checks(run_log, cplan, lines, checker, v3_hud(cplan, m, prof, hud6), out_dir, log)
    checks["captions"], more, cflags = caption_coverage(run_log, cplan, lines, out_dir / "captions.ass",
                                                        checks["caption_checks"])
    checks["caption_checks"] += more
    flags += cflags
    (out_dir / "checks.json").write_text(json.dumps(checks, indent=2, ensure_ascii=False), encoding="utf-8")
    bad = [c for c in checks["caption_checks"] if not c["ok"]]
    if bad:
        raise V3Failed("\n  ".join(f"caption \"{c['word']}\" at {c['t']:.1f} s (line {c['line']}): {c['why']}"
                                   for c in bad), checks)
    (out_dir / "claims.txt").write_text(truth.claims_text(short, m, claims, facts), encoding="utf-8")
    if first_note:
        flags.append(f"FIRST FRAME: {first_note}")
    return {"length_s": round((run_log.get("durations") or {}).get("video_s", plan["total_s"]), 2), "flags": flags,
            "line_spans": spans, "music": Path((run_log.get("music") or {}).get("file") or "").name or None,
            "shown": [[m["video"], s["from"], s["to"]] for s in plan["shots"] if not s.get("replay")], "engine": "v4",
            "game_sound": True, "checks": checks, "payoff": checks["payoff"], "replay": replay}


def explainer_error():
    import explainer
    return explainer.ScriptError


def short_folder(folder, n, title):
    return Path(folder) / f"{n}-{footage_short.make_short.slug_of(title or 'short')[:40]}"


def render_one(short, cand, folder, facts, today, indexes, hook_choice=None, no_cold_open=False, log=print,
               checker=None, timing=None, narrated_render=None, action_render=None, engine="v4", synth=None,
               v2_render=None, v3_render=None, used=None, hud6=None, v4_parts=None):
    """One Short, start to finish. Returns its entry for render.json (status READY or FAILED, with the problems).
    v4_parts: engine v4's makers, replaced in tests (render_v4's keyword arguments)."""
    m = moment_for(cand, short)
    n = m["short"]
    hooks_list = short.get("hooks") or []
    star = short.get("star")
    if hook_choice is not None:
        star = hook_choice - 1
        short = dict(short, star=star)
    entry = {"short": n, "moment": m["id"], "type": m["type"], "status": "FAILED", "problems": [],
             "flags": list(m.get("flags", [])), "hooks": hooks_list, "star": star, "title": short.get("title"),
             "video": m["video"], "window": m["window"]}
    if m.get("bank_id"):
        entry["bank_id"] = m["bank_id"]
    out = short_folder(folder, n, short.get("title"))
    entry["folder"] = str(out)
    problems = []
    if short.get("start_s") is not None:
        m, problems = trimmed(m, short["start_s"])
        entry["window"] = m["window"]
    more, flags, _ = truth.check_short(short, m, facts, today, engine=engine)
    problems += more
    peak, peak_problems = (check_peak(short, m, no_cold_open) if m["type"] == "action" and engine not in ("v3", "v4")
                           else (None, []))
    problems += peak_problems
    if problems:
        entry["problems"] = problems
        return entry
    out.mkdir(parents=True, exist_ok=True)
    hook = hooks_list[star] if isinstance(star, int) and 0 <= star < len(hooks_list) else None
    try:
        if short.get("start_s") is not None and checker is not None:
            gore = safety_checks.bad_start(checker, recs_stub(m), m["path"],
                                           safety_checks.first_seconds(m["window"][0]), out / "safety")
            if gore:
                raise RenderProblem("start_s: the new first 7 s show blood or swearing ("
                                    + "; ".join(safety_checks.fmt(gore)) + "); pick another start")
        if engine == "v4":
            done = render_v4(short, m, out, facts, today, indexes, used or (), log, checker, hud6, **(v4_parts or {}))
            flags += done.pop("flags", [])
            peak = None
        elif engine == "v3":
            done = render_v3(short, m, out, facts, today, indexes, used or (), log, timing, v3_render, checker, hud6)
            flags += done.pop("flags", [])
            peak = None
        elif m["type"] == "action":
            if peak is not None and checker is not None:
                cold = safety_checks.first_seconds(m["window"][0], peak)[0]
                gore = safety_checks.bad_start(checker, recs_stub(m), m["path"], [cold], out / "safety")
                if gore:
                    flags.append("NO COLD OPEN: the auto peak shows blood or swearing ("
                                 + "; ".join(safety_checks.fmt(gore)) + ")")
                    peak = None
            done = (action_render or render_action)(short, m, out, hook, peak, log)
            if peak is not None:
                flags.append(f"AUTO PEAK at {library.hms(peak)} ({peak:.1f} s of {m['video']}), from "
                             f"{short['peak'].get('evidence')}: the cold open and the zoom use it. Wrong? Re-render "
                             "without it (command below)")
        elif engine == "v1":
            done = render_narrated(short, m, out, facts, today, indexes, log, timing, narrated_render)
            flags += done.pop("flags", [])
        else:
            done = render_story(short, m, out, facts, today, indexes, folder, cand.get("dry_run"), log, synth,
                                v2_render)
            flags += done.pop("flags", [])
    except RenderProblem as e:
        entry["problems"] = [str(e)]
        if getattr(e, "checks", None):
            entry["checks"] = e.checks
        return entry
    tp = safety_checks.guard_true_peak(out / "short.mp4", log=lambda s: log("  " + s))
    (out / "upload.txt").write_text(footage_short.make_short.upload_text(short), encoding="utf-8")
    if any(e["kind"] == "event" and e["value"] == "crash" for e in m["evidence"]):
        flags.append("CRASH: check that it looks cinematic, not clumsy")
    game_sound = engine != "v3" or done.get("game_sound")
    heard = speech_check(out / "short.mp4", log)
    if heard is None:
        if game_sound:
            flags.append("SOUND: the game's own sound is in (car radio, mission music, dialogue). Spoken swearing is "
                         "NOT checked (no speech-to-text): listen once before uploading")
    else:
        if heard["early"]:                    # FAILED with its metadata (moment, folder, hooks), not a bare stub
            entry["problems"] = ["spoken swearing in the first 7 s (speech check): "
                                 + ", ".join(f"{w!r} at {t:.1f} s" for t, w in heard["early"])
                                 + "; pick another start or footage without that line"]
            return entry
        for t, w in heard["later"]:
            flags.append(f"SWEARING: {w!r} is said at {t:.1f} s (speech check); cut it or mute that part")
        if game_sound and not heard["later"]:
            flags.append(f"SOUND: the game's own sound is in; the speech check heard no swearing "
                         f"({heard['words']} words). Listen once anyway: it can miss mumbled words")
    entry.update(status="READY", flags=entry["flags"] + flags, true_peak=tp, peak_s=peak, hook=hook, **done,
                 files={k: str(out / k) for k in ("short.mp4", "upload.txt", "claims.txt") if (out / k).is_file()})
    return entry


def speech_check(path, log=print, check=None):
    """launch-fixes: speech.swearing on a finished Short, or None where faster-whisper isn't installed (or fails:
    the Short then keeps the "listen once" flag)."""
    import speech
    if check is None:
        if not speech.available():
            return None
        check = speech.swearing
    try:
        return check(path)
    except Exception as e:                      # a broken speech check must not stop the night
        log(f"  Note: the speech check failed ({e}); listen to this Short once")
        return None


def recs_stub(m):
    """What safety.gore_in needs to read the index's lines for one moment (they were saved with its evidence)."""
    seen = [{"t": e["from_s"], "notable": e["value"]} for e in m["evidence"] if e["kind"] == "seen"]
    return {"sheets": seen, "every": (seen[1]["t"] - seen[0]["t"]) if len(seen) > 1 else index.EVERY_S}


def commands(folder, n, out=None):
    py = r".\.venv-tts\Scripts\python.exe launch.py render"
    return {"no_cold_open": f'{py} --run "{folder}" --short {n} --no-cold-open',
            "posted": rf'.\.venv\Scripts\python.exe bank.py posted "{out}"' if out else None,
            "hook": [f'{py} --run "{folder}" --short {n} --hook {k}' for k in (1, 2, 3)]}


def kept_results(old, written):
    """The earlier render.json entries a render keeps (a --short N render keeps the others): only those whose Short
    is still in shorts.json (launch-fixes: a renumbered Short left its old FAILED entry on the review page)."""
    now = {s.get("short") for s in written.get("shorts", []) if isinstance(s, dict)}
    return {e["short"]: e for e in (old or {}).get("shorts", []) if e.get("short") in now}


def render(folder=None, only=None, hook_choice=None, no_cold_open=False, shorts_dir=SHORTS_DIR, today=None, log=print,
           facts=None, checker="auto", timing=None, narrated_render=None, action_render=None, indexes=None,
           engine="v4", synth=None, v2_render=None, v3_render=None, v4_parts=None):
    """The render stage for a run folder (default: the newest). Every Short in shorts.json, or only --short N.
    Returns the render.json data; review.html is written next to it."""
    import facts as facts_file
    folder = Path(folder) if folder else latest_run(shorts_dir)
    cand = read_json(folder / "candidates.json")
    written = read_json(folder / SHORTS_FILE)
    if cand is None:
        raise LaunchError(f"no candidates.json in {folder}: run launch.py prepare first")
    if not isinstance(written, dict) or not isinstance(written.get("shorts"), list):
        raise LaunchError(f"{folder / SHORTS_FILE} is missing or has no \"shorts\" list (Claude writes it)")
    today = today or date.today().isoformat()
    facts = facts if facts is not None else facts_file.load_facts()
    indexes = indexes if indexes is not None else library.load_library(cand["settings"]["library"])
    if checker == "auto":
        try:
            checker = safety_checks.Checker(cand["game_name"], index.LIBRARY / "_cache", log=log)
        except vlm.VlmError as e:
            log(f"  Note: the auto peak's gore check is off ({e}); the cold open is flagged for a look")
            checker = None
    results = kept_results(read_json(folder / RENDER_LOG), written)
    for short in written["shorts"]:
        if only is not None and short.get("short") != only:
            continue
        log(f"Short {short.get('short')}: {short.get('title')}")
        try:
            mine_name = Path(short_folder(folder, short.get("short"), short.get("title"))).name + "@" + folder.name
            used = [] if cand.get("dry_run") else used_by_others(shorts_dir, mine_name, folder.name)
            entry = render_one(short, cand, folder, facts, today, indexes, hook_choice if only else None,
                               no_cold_open and only is not None, log, checker, timing, narrated_render, action_render,
                               engine, synth, v2_render, v3_render, used, v4_parts=v4_parts)
        except RenderProblem as e:
            entry = {"short": short.get("short"), "status": "FAILED", "problems": [str(e)], "flags": []}
        entry["commands"] = commands(folder, entry["short"], entry.get("folder"))
        results[entry["short"]] = entry
        if entry["status"] == "READY":
            log(f"  READY: {entry['files']['short.mp4']}  ({entry['length_s']:.1f} s, true peak "
                f"{entry['true_peak']['after']:+.1f} dBTP)")
            if not cand.get("dry_run"):
                # made, not posted: free again, ranked lower; "bank.py posted" locks it once the owner has posted it
                footage_short.save_used(shorts_dir, Path(entry["folder"]).name + "@" + folder.name, entry["shown"],
                                        "rendered")
        else:
            log("  FAILED:\n    " + "\n    ".join(entry["problems"]))
    data = {"folder": str(folder), "rendered": datetime.now().isoformat(timespec="seconds"),
            "dry_run": cand.get("dry_run"), "shorts": [results[k] for k in sorted(results)]}
    write_json(folder / RENDER_LOG, data)
    page = write_review(folder, cand, written, data)
    if not cand.get("dry_run"):
        note_run(shorts_dir, folder, [r["path"] for r in cand["recordings"]], "rendered")
    log(f"Review page: {page}")
    return data


def checks_block(checks):
    """The engine v3 checks on the review page: how much of the Short is full-screen gameplay, every caption checked
    against its frame, and every shot rejected (too dark, empty, game text under YouTube's title)."""
    if not checks:
        return ""
    out = []
    if checks.get("full_screen") is not None:
        out.append(f"<p>Full-screen gameplay: <b>{checks['full_screen']:.0%}</b> of the Short</p>")
    caps, seen = checks.get("caption_checks") or [], checks.get("captions")
    if seen:
        out.append(f"<p>Captions: {seen['shown']} words shown, {seen['over_a_shot']} over a shot, "
                   f"<b>{seen['tested']}</b> checked against the picture"
                   + (f", {seen['on_card']} on the open card instead" if seen.get("on_card") else "") + "</p>")
    if caps:
        rows = "".join(f'<li class="{"ok" if c["ok"] else "bad"}">{"&#10003;" if c["ok"] else "&#10007;"} '
                       f'{c["t"]:.1f} s "{esc(c["word"])}" (line {c["line"]}): {esc(c["rule"])}'
                       + (f' &middot; {esc(c["why"])}' if c.get("why") else "")
                       + (f' &middot; saw: {esc(c["saw"])}' if c.get("saw") not in (None, "") and c["ok"] else "")
                       + "</li>" for c in caps)
        out.append(f"<h3>Caption = picture ({sum(c['ok'] for c in caps)} of {len(caps)} pass)</h3>"
                   f"<ul class=\"checks\">{rows}</ul>")
    rej = checks.get("rejected_shots") or []
    if rej:
        rows = "".join(f'<li class="bad">line {r["line"]}, beat {r["beat"]} ({esc(r["video"])} at {library.hms(r["at"])})'
                       f': {esc("; ".join(r["why"]))}</li>' for r in rej)
        out.append(f"<h3>Shots rejected</h3><ul class=\"checks\">{rows}</ul>")
    return "".join(out)


def esc(text):
    import html
    return html.escape(str(text if text is not None else ""))


def rel(folder, path):
    """A path for the page: relative to the run folder when it is inside it, else a file:/// link."""
    from urllib.parse import quote
    p = Path(path)
    try:
        return quote(p.resolve().relative_to(Path(folder).resolve()).as_posix())
    except ValueError:
        return "file:///" + quote(str(p.resolve()).replace("\\", "/"), safe="/:")


def evidence_picture(folder, e):
    """The evidence item's picture copied next to the page (index sheets live in the library); None without one."""
    src = e.get("picture")
    if not src or not Path(src).is_file():
        return None
    out = Path(folder) / "evidence" / f"{Path(src).parent.parent.name}_{Path(src).name}"
    out.parent.mkdir(exist_ok=True)
    if not out.exists():
        shutil.copyfile(src, out)
    return rel(folder, out)


def posted_it(entry):
    """The line to run once this Short is on YouTube (bank.py posted): only then is its footage locked."""
    cmd = (entry.get("commands") or {}).get("posted")
    return (f'<h3>Posted it?</h3><p class="meta">Run this once it is on YouTube. It locks the footage, so it never comes '
            f'back, and adds its topic to your posted list. Until then the footage stays free.</p>'
            f'<code class="cmd">{esc(cmd)}</code><button data-copy="{esc(cmd)}">Copy</button>') if cmd else ""


def write_review(folder, cand, written, data):
    """review.html in the run folder: what to do, then one card per Short with the video, its flags, the 3 hooks
    (starred one marked, a command to swap), the script next to its evidence, the evidence pictures, the upload text
    and the commands to fix things. Plain English: the owner reads it at 3 AM."""
    folder = Path(folder)
    shorts = {s.get("short"): s for s in written.get("shorts", [])}
    cards = []
    for entry in data["shorts"]:
        n = entry["short"]
        m = next((p for p in cand["picks"] if p["short"] == n), {})
        short = shorts.get(n, {})
        ok = entry["status"] == "READY"
        by_id = {e["id"]: e for e in m.get("evidence", [])}
        video = (f'<video controls preload="metadata" src="{rel(folder, entry["files"]["short.mp4"])}"></video>'
                 if ok else '<div class="novideo">NOT RENDERED</div>')
        flags = "".join(f'<li class="{"red" if f.split(":")[0] in RED_FLAGS else "amber"}">{esc(f)}</li>'
                        for f in entry.get("flags", []))
        problems = "".join(f"<li>{esc(p)}</li>" for p in entry.get("problems", []))
        star = entry.get("star") if isinstance(entry.get("star"), int) else short.get("star")
        hooks_html = "".join(
            f'<li class="{"star" if k == star else ""}">{"&#11088; " if k == star else ""}{esc(h)}'
            + ("" if k == star else f'<br><code class="cmd">{esc(entry["commands"]["hook"][k])}</code>') + "</li>"
            for k, h in enumerate(entry.get("hooks") or short.get("hooks") or []))
        lines = []
        for k, line in enumerate(short.get("lines") or [], 1):
            chips = "".join(f'<span class="chip">{esc(i)} {esc(by_id[i]["kind"])}: {esc(by_id[i]["value"])} '
                            f'<small>{library.hms(by_id[i]["from_s"])}</small></span>'
                            for i in line.get("evidence", []) if i in by_id)
            spans = entry.get("line_spans") or []
            when = (f' <small>(footage {library.hms(spans[k - 1][0])}-{library.hms(spans[k - 1][1])})</small>'
                    if k - 1 < len(spans) else "")
            facts_html = f' <span class="chip fact">facts: {esc(", ".join(line["facts"]))}</span>' if line.get("facts") else ""
            role = ("PAYOFF &middot; " if line.get("payoff") else "") + ("LOOP &middot; " if line.get("loop") else "")
            teaches = (f'<br><span class="claim">teaches: {esc(line["teaches"])}</span>' if line.get("teaches") else
                       f'<br><span class="claim">answered by line {esc(line.get("answered_by"))}</span>'
                       if line.get("claim") == "ask" else "")
            off = [x for x in entry.get("anchors") or [] if x["line"] == k and not x["ok"]]
            lines.append(f'<li><b>{esc(line.get("say"))}</b>{when}<br><span class="claim">{role}{esc(line.get("claim"))}'
                         f'</span> {chips}{facts_html}{teaches}'
                         + "".join(f'<br><span class="claim red">"{esc(x["word"].lower())}" not on screen with its '
                                   f'evidence</span>' for x in off) + '</li>')
        cited = [by_id[i] for line in short.get("lines") or [] for i in line.get("evidence", []) if i in by_id]
        shown = cited or [e for e in m.get("evidence", []) if e["kind"] in ("event", "stars", "police")] or \
            [e for e in m.get("evidence", []) if e["kind"] == "seen"]
        pics = []
        for e in list({e["id"]: e for e in shown}.values())[:8]:
            pic = evidence_picture(folder, e)
            img = f'<img loading="lazy" src="{pic}" alt="{esc(e["id"])}">' if pic else '<div class="nopic">HUD reading</div>'
            pics.append(f'<figure>{img}<figcaption>{esc(e["id"])} {esc(e["kind"])}: {esc(e["value"])}<br>'
                        f'<small>{library.hms(e["from_s"])}-{library.hms(e["to_s"])} &middot; {esc(e["source"])}</small>'
                        f'</figcaption></figure>')
        sheet = f'<img class="sheet" loading="lazy" src="{rel(folder, m["sheet"])}" alt="frames of the footage">' \
            if m.get("sheet") else ""
        upload = ""
        if ok and Path(entry["files"].get("upload.txt", "")).is_file():
            upload = Path(entry["files"]["upload.txt"]).read_text(encoding="utf-8")
        tp = entry.get("true_peak") or {}
        meta = (f'{esc(entry.get("type", "").upper())} &middot; {esc(m.get("video"))} '
                f'{library.hms(m.get("window", [0, 0])[0])}-{library.hms(m.get("window", [0, 0])[1])} &middot; '
                + (f'{entry["length_s"]:.1f} s &middot; true peak {tp.get("after", 0):+.1f} dBTP &middot; ' if ok else "")
                + f'{"NO-HUD MODE" if m.get("mode") == "nohud" else "HUD rules on"} &middot; why this type: '
                f'{esc(m.get("type_why"))}')
        if m.get("pick_why"):
            meta += f'<br>why this moment: {esc(m["pick_why"])}'
        if short.get("format"):
            story = (f'<b>{esc(short["format"])}</b> ({esc({**truth.FORMATS, **truth.V3_FORMATS}.get(short["format"], ""))}): '
                     f'{esc(short.get("format_why"))}')
            if isinstance(entry.get("payoff"), dict):
                pay = entry["payoff"]
                story += (f' &middot; <b>payoff</b>: {esc(pay.get("what"))} ({library.hms(pay.get("from", 0))}-'
                          f'{library.hms(pay.get("to", 0))}, {pay.get("at_s", 0):.1f} s into the Short)')
            if entry.get("payoff_at") is not None:
                story += f' &middot; payoff at {entry["payoff_at"]:.0%}'
            if entry.get("pops") is not None:
                story += (f' &middot; {entry["pops"]} pop-ins (every {entry.get("pop_interval_s")} s), background shots '
                          f'{entry.get("background_avg_s")} s')
            gaps = "; ".join(f'{g["from_s"]:.1f}-{g["to_s"]:.1f} s: {g["why"]}' for g in entry.get("gaps_over_2s") or [])
            meta += f'<br>Story: {story}' + (f'<br>Gaps over 2 s: {esc(gaps)}' if gaps else "")
        checks_html = checks_block(entry.get("checks"))
        source = rel(folder, m["path"]) + f'#t={m["window"][0]:.1f},{m["window"][1]:.1f}' if m.get("path") else ""
        claims = (f' &middot; <a href="{rel(folder, entry["files"]["claims.txt"])}">claims.txt</a>'
                  if ok and "claims.txt" in entry.get("files", {}) else "")
        cards.append(f"""
<article class="{'ok' if ok else 'bad'}">
 <h2>Short {n}: {esc(entry.get('title') or short.get('title'))} <span class="status">{esc(entry['status'])}</span></h2>
 <p class="meta">{meta}</p>
 <div class="grid">
  <div>{video}<p><a href="{source}">Play the source footage</a>{claims}</p></div>
  <div>
   {'<h3>Problems (not rendered)</h3><ul class="problems">' + problems + '</ul>' if problems else ''}
   <h3>Flags: check these</h3><ul class="flags">{flags or '<li class="ok">none</li>'}</ul>
   {checks_html}
   <h3>Hooks (&#11088; = on the Short)</h3><ul class="hooks">{hooks_html}</ul>
   {'<h3>Script and its evidence</h3><ol class="lines">' + ''.join(lines) + '</ol>' if lines else ''}
   {f'<h3>Upload text</h3><pre class="upload">{esc(upload)}</pre><button data-copy="{esc(upload)}">Copy upload text</button>' if upload else ''}
   {posted_it(entry) if ok else ''}
   {f'<h3>Cold open wrong?</h3><code class="cmd">{esc(entry["commands"]["no_cold_open"])}</code>' if entry.get('peak_s') is not None else ''}
  </div>
 </div>
 <h3>Evidence</h3><div class="pics">{''.join(pics)}</div>
 {f'<h3>The footage, a frame every 2 s</h3>{sheet}' if sheet else ''}
</article>""")
    run = cand
    recs = "".join(f"<li>{esc(r['name'])} ({library.hms(r['duration_s'])}): "
                   f"{'NO-HUD MODE' if r['mode'] == 'nohud' else 'HUD rules on'}"
                   + (f" &middot; {esc('; '.join(r['hud_check']['reasons']))}" if (r.get('hud_check') or {}).get('reasons')
                      else "") + "</li>" for r in run["recordings"])
    timing = ", ".join(f"{k} {v / 60:.1f} min" if v >= 60 else f"{k} {v:.0f} s" for k, v in run.get("timing_s", {}).items())
    hud = run.get("hud") or {}
    banner = ""
    if hud.get("mode") in ("nohud", "partial"):
        lost = "".join(f"<li>{esc(stem)}: {esc(why)}</li>" for stem, why in (hud.get("nohud_recordings") or {}).items())
        banner = (f'<div class="hudwarn"><b>{"HUD NOT FOUND" if hud["mode"] == "nohud" else "HUD NOT READ ON SOME RECORDINGS"}'
                  f'</b>: no wanted-star counts {"anywhere" if hud["mode"] == "nohud" else "for these recordings"}, so no '
                  f'Short mentions stars there and labels come from the vision model alone. Why: {esc(hud.get("why", ""))}'
                  f'{"<ul>" + lost + "</ul>" if lost else ""}Full log: <code>{esc(hud.get("log", ""))}</code></div>')
    page = REVIEW_PAGE.format(
        hudwarn=banner, title=esc(folder.name), dry=('<p class="dry">DRY RUN: nothing was logged as used footage; these Shorts are '
                                    'for testing.</p>' if run.get("dry_run") else ""),
        calibration=esc(f"{run['calibration']['profile']}: {run['calibration']['why']}"), recordings=recs,
        timing=esc(timing), cards="".join(cards),
        ready=sum(1 for e in data["shorts"] if e["status"] == "READY"), total=len(data["shorts"]))
    out = folder / "review.html"
    out.write_text(page, encoding="utf-8")
    return out


RED_FLAGS = ("NO-HUD MODE", "WEAK MOMENT", "BLOOD", "SUBTITLES", "PROFANITY", "AUTO PEAK", "NO COLD OPEN",
             "REPORTED FACT", "SOUND")

REVIEW_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Launch review {title}</title>
<style>
:root {{ --bg:#0f1115; --panel:#171a21; --line:#2a2f3a; --text:#e6e8ec; --muted:#9aa3b2; --red:#ef4444; --amber:#f59e0b;
  --green:#22c55e; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui, "Segoe UI", sans-serif; }}
main {{ max-width:1200px; margin:0 auto; padding:16px; }}
h1 {{ font-size:22px; margin:0 0 6px; }} h2 {{ font-size:19px; margin:0 0 4px; }} h3 {{ font-size:14px; margin:14px 0 4px;
  color:var(--muted); text-transform:uppercase; letter-spacing:.05em; }}
.steps {{ background:#13233a; border:1px solid #2b4a75; border-radius:8px; padding:12px 16px; }}
.hudwarn {{ background:#3a1518; border:2px solid var(--red); border-radius:8px; padding:12px 16px; margin:0 0 12px; }}
.steps li {{ margin:4px 0; }}
.dry {{ background:#3a2a10; border:1px solid var(--amber); border-radius:6px; padding:8px 12px; }}
article {{ background:var(--panel); border:1px solid var(--line); border-left:6px solid var(--green); border-radius:8px;
  padding:14px; margin:18px 0; }}
article.bad {{ border-left-color:var(--red); }}
.status {{ font-size:13px; padding:2px 8px; border-radius:10px; background:#1f3a29; color:#86efac; vertical-align:middle; }}
article.bad .status {{ background:#3a1518; color:#fca5a5; }}
.meta {{ color:var(--muted); font-size:13px; margin:0 0 8px; }}
.grid {{ display:grid; grid-template-columns:minmax(0, 340px) 1fr; gap:18px; }}
video {{ width:100%; max-width:340px; aspect-ratio:9/16; background:#000; border-radius:6px; }}
.novideo {{ aspect-ratio:9/16; display:grid; place-items:center; background:#0b0d12; border-radius:6px; color:var(--red); }}
ul, ol {{ margin:0; padding-left:20px; }}
.flags li.red {{ color:#fca5a5; }} .flags li.amber {{ color:#fde68a; }} .flags li.ok {{ color:#86efac; }}
.problems li {{ color:#fca5a5; }}
.hooks li.star {{ font-weight:700; color:#fde68a; }}
.lines li {{ margin:6px 0; }} .claim {{ font-size:12px; color:var(--muted); }} .claim.red {{ color:#fca5a5; }}
.chip {{ display:inline-block; font-size:12px; background:#222838; border:1px solid var(--line); border-radius:10px;
  padding:0 7px; margin:2px 2px 0 0; }} .chip.fact {{ background:#2a2338; }}
code.cmd {{ display:block; font-size:12px; background:#0b0d12; border:1px solid var(--line); border-radius:4px; padding:4px 6px;
  margin:3px 0; overflow-x:auto; white-space:nowrap; }}
pre.upload {{ white-space:pre-wrap; background:#0b0d12; border:1px solid var(--line); border-radius:4px; padding:8px; }}
button {{ font:inherit; color:var(--text); background:#222838; border:1px solid var(--line); border-radius:4px; padding:4px 12px;
  cursor:pointer; }}
.pics {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:10px; }}
figure {{ margin:0; }} figure img {{ width:100%; border-radius:4px; display:block; }}
.nopic {{ height:120px; display:grid; place-items:center; background:#0b0d12; border-radius:4px; color:var(--muted); }}
figcaption {{ font-size:12px; color:var(--muted); }}
img.sheet {{ width:100%; border-radius:4px; }}
a {{ color:#93c5fd; }}
@media (max-width:800px) {{ .grid {{ grid-template-columns:1fr; }} }}
</style></head><body><main>
<h1>Launch review: {title}</h1>
{hudwarn}
{dry}
<div class="steps"><b>What to do (about 5 minutes per Short). {ready} of {total} Shorts are ready.</b>
<ol>
<li>Watch the Short with the sound on.</li>
<li>Read its flags. Red ones need a look before you upload.</li>
<li>For a narrated Short, read each line next to its evidence: every line must be true of what you see.</li>
<li>Wrong hook or cold open? Copy the command under it, paste it in PowerShell in C:\\AI\\gta-clipper, press Enter, then
reload this page.</li>
<li>Upload it yourself with the upload text (Copy button). Nothing is uploaded automatically.</li>
<li>When it is on YouTube: copy the line under "Posted it?", paste it in PowerShell and press Enter. Its footage is then
locked. A Short you don't post locks nothing.</li>
</ol></div>
<p class="meta">Profile {calibration}. Recordings:</p><ul class="meta">{recordings}</ul>
<p class="meta">Time: {timing}</p>
{cards}
</main>
<script>
document.querySelectorAll("button[data-copy]").forEach(b => b.addEventListener("click", () => {{
  navigator.clipboard.writeText(b.dataset.copy).then(() => {{ b.textContent = "Copied"; }});
}}));
</script>
</body></html>
"""


def all_used_spans(shorts_dir):
    """Every locked (video, start, end): what a posted Short showed (all of them, not only the last few: /launch never
    shows posted footage twice) and what the owner rejected. Footage a Short was only rendered from is free (PR 32)."""
    used = footage_short.load_used(shorts_dir)
    return [tuple(span) for s in used["shorts"] if footage_short.locked(s) for span in s.get("spans", [])] + [
        tuple(span) for r in used.get("rejected", []) for span in r.get("spans", [])]


def used_by_others(shorts_dir, mine_name, folder_name):
    """What one Short of a run may not show: all locked footage but its own, and what the run's other Shorts showed."""
    used = footage_short.load_used(shorts_dir)
    return [tuple(span) for s in used["shorts"] if s.get("name") != mine_name
            and (footage_short.locked(s) or str(s.get("name", "")).endswith("@" + folder_name))
            for span in s.get("spans", [])] + [tuple(sp) for r in used.get("rejected", []) for sp in r.get("spans", [])]


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
    p.add_argument("--count", type=int, default=3, help="how many Shorts to make (default 3, at least 1)")
    p.add_argument("--dry-run", action="store_true",
                   help="a trial: its own folder, profile and library; _used_footage.json is neither read nor written")
    p.add_argument("--date", help="the date in the folder name (default today)")
    r = sub.add_parser("render", help="check shorts.json, render every Short, write review.html")
    r.add_argument("--run", type=Path, help="the run folder (default: the newest /launch run)")
    r.add_argument("--short", type=int, help="only this Short (its number on the review page)")
    r.add_argument("--hook", type=int, choices=(1, 2, 3), help="with --short: use this hook instead of the starred one")
    r.add_argument("--no-cold-open", action="store_true", help="with --short: no cold open and no zoom")
    q = sub.add_parser("repick", help="the picking step again on an old run's cached data (nothing indexed again): "
                                      "old picks against new ones, in a new folder")
    q.add_argument("--run", type=Path, required=True, help="the run folder to re-pick (it is not changed)")
    q.add_argument("--count", type=int, help="how many picks (default: the run's own count)")
    q.add_argument("--posted-now", action="store_true",
                   help="today's view: today's posted topics and all the footage used since (default: both as they "
                        "were when the run was prepared)")
    q.add_argument("--out", type=Path, help="where to write repick.json and repick.txt (default: <run>-repick)")
    q.add_argument("--bank", action="store_true",
                   help="pick from the content bank's moments of that run's recordings (labels from their peaks)")
    f = sub.add_parser("frame", help="a gridded frame of a pick's recording, to read a circle's box from")
    f.add_argument("--run", type=Path, help="the run folder (default: the newest /launch run)")
    f.add_argument("--short", type=int, required=True, help="the pick's number")
    f.add_argument("--at", type=float, nargs="+", required=True, help="recording time(s) in seconds")
    r.add_argument("--engine", choices=ENGINES, default="v4",
                   help="v4 (default, editor-v3): payoff first, shots as spans, no second twice, fixed and measured-"
                        "steady picture, the voice placed into the picture; "
                        "v3: every Short the Step 0 sample's way (hook card, voice, captions, close-ups); "
                        "v2: PR 26's story layer (narrated only); v1: PR 25's explainer.py render")
    args = ap.parse_args()
    fastff.use()
    try:
        if args.stage == "frame":
            folder = args.run or latest_run()
            cand = read_json(Path(folder) / "candidates.json") or {}
            m = next((p for p in cand.get("picks", []) if p["short"] == args.short), None)
            if m is None:
                raise LaunchError(f"no pick {args.short} in {folder}")
            (Path(folder) / "moments").mkdir(exist_ok=True)
            for at in args.at:
                out = Path(folder) / "moments" / f"{m['id']}_grid_{at:.1f}.jpg"
                print(out if grid_frame(m["path"], at, out) else f"ERROR: no frame at {at} s")
        elif args.stage == "repick":
            with heavy.job("launch.py repick", log=lambda m: print(m, flush=True)):
                import bank
                repick(args.run, args.count, args.posted_now, out=args.out, log=lambda m: print(m, flush=True),
                       bank_dir=bank.BANK if args.bank else None)
        elif args.stage == "prepare":
            with heavy.job("launch.py prepare", log=lambda m: print(m, flush=True)):
                import bank
                prepare(args.game, args.profile, args.videos, args.every, args.dry_run, args.input,
                        count=max(1, args.count), day=args.date, slots=args.star_slots,
                        log=lambda m: print(m, flush=True),
                        bank_dir=bank.BANK if args.game == bank.GAME and not args.dry_run and not args.videos else None)
        else:
            if (args.hook or args.no_cold_open) and args.short is None:
                ap.error("--hook and --no-cold-open need --short N")
            with heavy.job("launch.py render", log=lambda m: print(m, flush=True)):
                data = render(args.run, args.short, args.hook, args.no_cold_open, log=lambda m: print(m, flush=True),
                              engine=args.engine)
            if any(e["status"] != "READY" for e in data["shorts"]):
                sys.exit(1)
    except (LaunchError, index.IndexError_, safety_checks.SafetyError, heavy.Busy) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    sys.modules["launch"] = sys.modules["__main__"]      # bank.py and safety.py import launch: this one, not a second copy
    main()
