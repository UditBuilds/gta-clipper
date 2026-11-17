"""Safety checks for /launch (PR 25), all in code: what a Short may never show, and what its sound may never do.

1. A safety question to the vision model (prompts/safety_v1.txt) on every finalist's footage, as 4-frame sheets:
   a frame every second (every half second in NO-HUD mode, where this is the only check). It asks: phone on screen,
   menu or map, loading or black, cutscene, blood or gore, subtitles. The first four block the footage they are in;
   the phone is caught this way with no phone calibration at all. Subtitles and blood after the first 7 s are flags
   for the owner.
2. Blood or gore in the first 7 s of a Short: the first 7 s of each type's footage are asked at 2 frames a second, and
   the index's own lines ("blood", "body") count too. Gore there moves the start 7 s later (once), or drops that type.
   The cold open (the auto peak) is checked again at render time with first_seconds().
3. Footage an earlier Short showed (_used_footage.json, pinned footage too) is never used: launch.mine() leaves it
   out and check_moment() checks again.
4. Audio true peak below -1.0 dBTP on every render: guard_true_peak() measures with ffmpeg's EBU R128 meter
   (ebur128=peak=true), and when the peak is higher, re-encodes only the audio through a true-peak limiter and
   measures again, lower each time, and fails if it never gets there.

Standard library + ffmpeg.
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import clip
import footage_short
import index
import library
import vlm

PROMPT_VERSION = "safety_v1"
BLOCKING = ("phone_screen", "menu_or_map", "loading_or_black", "cutscene")
WHAT = {"phone_screen": "phone on screen", "menu_or_map": "menu or map", "loading_or_black": "loading or black screen",
        "cutscene": "cutscene", "blood_or_gore": "blood or gore", "subtitles": "subtitles"}
SCHEMA = {"type": "object",
          "properties": {"note": {"type": "string"}, **{k: {"type": "boolean"} for k in WHAT}},
          "required": ["note", *WHAT]}
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 4096, "num_predict": 120}
EVERY_S = 1.0             # a frame every second...
EVERY_NOHUD_S = 0.5       # ...every half second when the HUD rules are off
FIRST_S = 7.0             # no blood or gore in a Short's first seconds
FIRST_EVERY_S = 0.5
GORE_WORDS = ("blood", "bleeding", "dead body", "body on the ground", "body on ground", "corpse", "gore", "wound")
SHEET_W = 640             # width of one frame on a sheet
TRUE_PEAK_MAX = -1.0      # dBTP
LIMIT_TRIES = (-2.0, -3.0, -4.0)   # true-peak targets tried in turn when a render is too loud


class SafetyError(Exception):
    """A render that can't be made safe (its true peak stays too high)."""


# --- The safety question ---

def parse_answer(content):
    """The model's reply as {note, phone_screen, ...}; ValueError if it doesn't fit SCHEMA."""
    try:
        a = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"reply is not JSON ({e})") from None
    if not isinstance(a, dict):
        raise ValueError("reply is not a JSON object")
    bad = [k for k in WHAT if not isinstance(a.get(k), bool)]
    if bad:
        raise ValueError(f"not true/false: {', '.join(bad)}")
    note = " ".join(str(a.get("note", "")).split())
    if not note:
        raise ValueError("empty note")
    return {"note": note, **{k: a[k] for k in WHAT}}


def load_prompt(game, version=PROMPT_VERSION):
    return index.load_prompt(game, version)


def sheet_times(a, b, every):
    """The frame times of a..b, one every `every` seconds, in sheets of 4: [(first, end, [times])]."""
    times, t = [], a
    while t < b - 1e-6:
        times.append(round(t, 3))
        t += every
    return [(ts[0], round(min(b, ts[-1] + every), 3), ts) for ts in (times[k:k + 4] for k in range(0, len(times), 4))]


def make_sheets(path, a, b, every, out_dir, width=SHEET_W):
    """JPEG sheets of a..b of a recording (2x2, a frame every `every` s, in reading order; the last one may end in
    black): [(from_s, to_s, file)]. One ffmpeg run for the whole stretch."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = sheet_times(a, b, every)
    stem = f"{Path(path).stem}_{a:.1f}_{b:.1f}_{every:g}".replace(".", "_")
    info = clip.probe(shutil.which("ffprobe"), Path(path))
    conv = "in_color_matrix=bt709:in_range=tv:" if clip.needs_range_fix(info["video_codec"], info["color_range"]) else ""
    graph = (f"select='gte(t-{a:.3f},selected_n*{every:g})',scale={width}:-2:{conv}out_color_matrix=bt601:"
             f"out_range=pc,tile=2x2")
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a + 0.05:.3f}", "-copyts",
                           "-i", str(path), "-vf", graph, "-fps_mode", "passthrough", "-q:v", "4",
                           "-start_number", "0", str(out_dir / f"{stem}_%03d.jpg")], capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"ffmpeg could not make safety sheets of {Path(path).name}: {done.stderr.strip()[-300:]}")
    files = sorted(out_dir.glob(f"{stem}_*.jpg"))
    return [(f0, f1, f) for (f0, f1, _), f in zip(plan, files)]


class Checker:
    """The safety question, asked through Ollama with the index's own call (index.ask_model) and cached like the
    index's answers. ask and sheets are swapped for stand-ins by the tests."""

    def __init__(self, game, cache_dir, model=index.MODEL, ask=index.ask_model, sheets=make_sheets, info=None,
                 log=print, version=PROMPT_VERSION):
        self.version = version
        self.prompt, self.sha = load_prompt(game, version)
        self.cache_dir, self.model, self.ask, self.sheets, self.log = Path(cache_dir), model, ask, sheets, log
        self.info = info if info is not None else vlm.check_ollama(model)
        self.calls, self.cached, self.seconds = 0, 0, 0.0

    def fields(self, path, f0, f1, every):
        video = Path(path)
        return {"video": video.name, "video_bytes": video.stat().st_size if video.exists() else None,
                "from_ms": round(f0 * 1000), "to_ms": round(f1 * 1000), "every_s": every, "frame_width": SHEET_W,
                "model": self.model, "model_digest": self.info.get("digest"), "prompt_version": self.version,
                "prompt_sha256": self.sha, "schema": SCHEMA, "options": OPTIONS}

    def look(self, path, a, b, every, work):
        """[{from_s, to_s, file, answer, error}] for footage a..b of a recording."""
        out = []
        for f0, f1, jpg in self.sheets(path, a, b, every, work):
            fields = self.fields(path, f0, f1, every)
            rec = vlm.cache_load(self.cache_dir, fields)
            if rec is None:
                rec = self.ask(self.model, self.prompt, jpg, self.info.get("capabilities", []), schema=SCHEMA,
                               parse=parse_answer, options=OPTIONS)
                self.calls += 1
                self.seconds += rec.get("wall_s") or 0.0
                if rec.get("error") is None:
                    vlm.cache_save(self.cache_dir, fields, rec)
            else:
                self.cached += 1
            out.append({"from_s": f0, "to_s": f1, "file": str(jpg), "answer": rec.get("answer"),
                        "error": rec.get("error")})
        return out

    def __call__(self, m, rec, folder):
        return check_moment(m, rec, folder, self)


def found(sheets, keys):
    """[(from_s, to_s, what)] for every sheet that says yes to one of keys; a sheet with no usable answer counts as
    a yes to all of them (unchecked footage is not safe footage)."""
    hits = []
    for s in sheets:
        a = s.get("answer")
        if a is None:
            hits.append((s["from_s"], s["to_s"], "no usable answer from the model"))
            continue
        hits += [(s["from_s"], s["to_s"], WHAT[k]) for k in keys if a.get(k)]
    return hits


def index_gore(rec, a, b):
    """The index's own lines that mention blood or a body in a..b: [(from_s, to_s, line)]."""
    out = []
    for s in rec["sheets"]:
        if s["t"] < b and a < s["t"] + rec["every"]:
            kept = library.positive_text(s.get("notable") or "")
            if any(w in kept for w in GORE_WORDS):
                out.append((s["t"], s["t"] + rec["every"], f"the index saw: {s['notable']}"))
    return out


def first_seconds(start, peak=None, cold=(0.15, 0.65)):
    """The stretches of the recording on screen in a Short's first FIRST_S seconds: the cold open round the peak
    (hooks.COLD_BEFORE_S before to hooks.COLD_AFTER_S after it), then the footage from its start."""
    if peak is None:
        return [(start, start + FIRST_S)]
    lead = cold[0] + cold[1]
    return [(peak - cold[0], peak + cold[1]), (start, start + FIRST_S - lead)]


def gore_in(checker, rec, path, spans, work):
    """Gore in these stretches (2 frames a second, plus the index's own lines): [(from_s, to_s, what)]."""
    hits = []
    for a, b in spans:
        hits += found(checker.look(path, a, b, FIRST_EVERY_S, work), ("blood_or_gore",))
        hits += index_gore(rec, a, b)
    return hits


def fmt(hits):
    return [f"{what} at {library.hms(a)}-{library.hms(b)}" for a, b, what in hits]


def check_moment(m, rec, folder, checker):
    """(passes, reasons) for one moment, and m["safety"] with what was found. Each type's footage (and the loop
    ending's lead for NARRATED) is asked about; a blocking answer anywhere in it drops that type, gore in its first
    7 s moves its start 7 s later once (or drops it). The types that pass go into m["safe_options"] as
    {type: [start, end]}, which launch.choose_types() keeps to."""
    import launch                                        # launch imports this module's Checker; no cycle at load
    opts = launch.type_options(rec, m)
    work = Path(folder) / "safety"
    every = EVERY_NOHUD_S if rec["mode"] == "nohud" else EVERY_S
    report = {"every_s": every, "types": {}, "flags": []}
    safe, reasons = {}, []
    windows = {k: opts[k]["window"] for k in ("action", "narrated") if k in opts}
    if not windows:
        return False, ["no stretch of clean footage long enough for either type"]
    lead = {"action": 0.0, "narrated": launch.LOOP_LEAD_S}
    for k, (a, b) in list(windows.items()):
        if not footage_short.clean(rec["idx"], a - lead[k], b):
            reasons.append(f"{k}: part of it is a cutscene, menu, phone call or loading screen in the index")
            del windows[k]
    if not windows:
        return False, reasons
    lo = min(w[0] - lead[k] for k, w in windows.items())
    hi = max(w[1] for w in windows.values())
    sheets = checker.look(rec["path"], lo, hi, every, work)
    report["sheets"] = sheets
    block = found(sheets, BLOCKING)
    for k, (a, b) in windows.items():
        here = [h for h in block if h[0] < b and a - lead[k] < h[1]]
        if here:
            report["types"][k] = {"dropped": fmt(here)}
            reasons.append(f"{k}: " + "; ".join(fmt(here)))
            continue
        need = launch.ACTION_LEN[0] if k == "action" else launch.NARRATED_MIN_S
        start, end, moved = a, b, False
        gore = gore_in(checker, rec, rec["path"], first_seconds(start), work)
        # Once, 7 s later: the end moves too where the clean stretch allows, so it stays long enough, and an action
        # clip must still hold its moment. The footage it gains at the end is asked about as well.
        later, longer = round(start + FIRST_S, 2), round(max(end, start + FIRST_S + need), 2)
        if (gore and longer <= m["span"][1] - footage_short.GUARD_S + 1e-6
                and (k != "action" or later < opts["anchor_s"])):
            extra = found(checker.look(rec["path"], end, longer, every, work), BLOCKING) if longer > end else []
            if not extra:
                start, end, moved = later, longer, True
                gore = gore_in(checker, rec, rec["path"], first_seconds(start), work)
        if gore:
            report["types"][k] = {"dropped": fmt(gore), "why": "blood or gore in the first 7 s"}
            reasons.append(f"{k}: blood or gore in the first 7 s ({'; '.join(fmt(gore))})")
            continue
        safe[k] = [start, end]
        report["types"][k] = {"window": [start, end], "moved_for_gore": moved}
    for a, b, what in found(sheets, ("subtitles",)):
        report["flags"].append(f"SUBTITLES at {library.hms(a)}-{library.hms(b)}: check the words (mission dialogue "
                               "can swear)")
    for a, b, what in found(sheets, ("blood_or_gore",)):
        report["flags"].append(f"BLOOD at {library.hms(a)}-{library.hms(b)} (after the first 7 s): check it")
    m["safety"], m["safe_options"] = report, safe
    return bool(safe), reasons


# --- Sound: true peak ---

def true_peak(path):
    """The true peak of a file's audio in dBTP (ffmpeg's EBU R128 meter, oversampled), or None with no audio."""
    done = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0?", "-af",
                           "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    found_tp = re.findall(r"True peak:\s+Peak:\s+(-?[\d.]+|-inf)\s+dBFS", done.stderr)
    if not found_tp:
        return None
    return float("-inf") if found_tp[-1] == "-inf" else float(found_tp[-1])


TRUE_PEAK_LIMITER = "aresample=192000,alimiter=limit={limit:.4f}:level=disabled:attack=1:release=50,aresample=48000"


def limiter(target_db):
    """ffmpeg filters for a true-peak limiter at target_db dBTP: alimiter only sees samples, so it runs at 4 times
    the sample rate (192 kHz), where the peaks between the 48 kHz samples are samples too."""
    return TRUE_PEAK_LIMITER.format(limit=10 ** (target_db / 20))


def limit_audio(src, dst, target_db):
    """dst = src with its audio through the true-peak limiter at target_db dBTP (re-encoded as AAC, which can add a
    little); the picture is copied as it is."""
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:v?", "-map", "0:a:0",
                           "-c:v", "copy", "-af", limiter(target_db), "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                           "-movflags", "+faststart", str(dst)], capture_output=True, text=True)
    if done.returncode:
        raise SafetyError(f"ffmpeg could not limit the audio of {Path(src).name}: {done.stderr.strip()[-300:]}")


def guard_true_peak(path, log=print, measure=true_peak, limit=limit_audio, tries=LIMIT_TRIES):
    """Make sure a finished render's audio peaks below TRUE_PEAK_MAX dBTP: measure; if it is higher, limit the audio
    (the picture is copied) to each target in turn and measure again. Returns {"before", "after", "passes",
    "limited_to"}; SafetyError if no try gets it below. A file with no audio passes as it is."""
    path = Path(path)
    before = measure(path)
    out = {"before": before, "after": before, "limited_to": None, "passes": 0}
    if before is None or before < TRUE_PEAK_MAX:
        return out
    for target in tries:
        with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
            fixed = Path(tmp) / path.name
            limit(path, fixed, target)
            after = measure(fixed)
            out["passes"] += 1
            if after is not None and after < TRUE_PEAK_MAX:
                fixed.replace(path)
                out.update(after=after, limited_to=target)
                log(f"  True peak {before:+.1f} dBTP was over {TRUE_PEAK_MAX:g}: limited to {after:+.1f} dBTP")
                return out
    raise SafetyError(f"{path.name}: the true peak is {before:+.1f} dBTP and stays over {TRUE_PEAK_MAX:g} dBTP after "
                      f"{len(tries)} tries with a limiter")
