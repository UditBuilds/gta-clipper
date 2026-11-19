"""Safety checks for /launch (PR 25), all in code: what a Short may never show, and what its sound may never do.

1. A safety question to the vision model (prompts/safety_v2.txt) on every finalist's footage, as 4-frame sheets:
   a frame every second (every half second in NO-HUD mode, where this is the only check). It asks: phone on screen,
   menu or map, loading or black, cutscene, blood or gore, subtitles. The first four block the footage they are in;
   the phone is caught this way with no phone calibration at all. Subtitles and blood after the first 7 s are flags
   for the owner.
2. Blood or gore, or swearing in the subtitles, in the first 7 s of a Short: the first 7 s of each type's footage
   are asked about at 2 frames a second, the index's own lines ("blood", "body") count too, and the subtitle band of
   every second is read by the model (subtitles_v1.txt) and matched against PROFANITY in code. Any of it there moves
   the start 7 s later (once), or drops that type. Swearing later in the Short is a PROFANITY flag. The cold open (the
   auto peak) and a later start (start_s) are checked again at render time. Swearing that is only spoken in the game's
   sound is NOT checked: there is no speech-to-text here, and every Short says so.
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

PROMPT_VERSION = "safety_v3"     # prompts/LOG.md: v2's questions, plus what the frames show (launch-fixes)
BLOCKING = ("phone_screen", "menu_or_map", "loading_or_black", "cutscene")
WHAT = {"phone_screen": "phone on screen", "menu_or_map": "menu or map", "loading_or_black": "loading or black screen",
        "cutscene": "cutscene", "blood_or_gore": "blood or gore", "subtitles": "subtitles"}
# What the frames show (safety_v3, launch-fixes): a label such as "crash" or "shootout" is kept only where the model
# sees it in the candidate's own frames, never from words alone.
SEEN = {"crash": "a crash", "shooting": "shooting", "police": "police", "explosion": "an explosion",
        "stunt": "a jump or a flip", "water": "swimming or a boat"}
SCHEMA = {"type": "object",
          "properties": {"note": {"type": "string"}, **{k: {"type": "boolean"} for k in WHAT},
                         **{k: {"type": "boolean"} for k in SEEN}},
          "required": ["note", *WHAT, *SEEN]}
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 4096, "num_predict": 120}
EVERY_S = 1.0             # a frame every second...
EVERY_NOHUD_S = 0.5       # ...every half second when the HUD rules are off
FIRST_S = 7.0             # no blood or gore in a Short's first seconds
FIRST_EVERY_S = 0.5
GORE_WORDS = ("blood", "bleeding", "dead body", "body on the ground", "body on ground", "corpse", "gore", "wound")
# Swear words, matched in the subtitles the model reads. Strong and moderate words and slurs; mild ones (damn, hell,
# crap) are left out. A word with a * in it ("f**k", "sh*t") is a censored swear and counts too.
PROFANITY = re.compile(
    r"(?<![a-z])(f+u+c+k\w*|\w*fuck\w*|motherf\w*|sh+i+t+\w*|bullshit\w*|bitch\w*|cunt\w*|asshole\w*|ass|asses|"
    r"dick|dicks|dickhead\w*|cock|cocks|cocksucker\w*|puss(?:y|ies)|bastard\w*|slut\w*|whore\w*|wank\w*|twat\w*|"
    r"prick\w*|nigg\w*|fag|fags|faggot\w*|retard\w*)(?![a-z])"
    r"|[a-z]+\*+[a-z*]*", re.IGNORECASE)
SUBTITLE_VERSION = "subtitles_v1"
SUBTITLE_SCHEMA = {"type": "object", "properties": {f"strip_{k}": {"type": "string"} for k in range(1, 5)},
                   "required": [f"strip_{k}" for k in range(1, 5)]}
SUBTITLE_OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 4096, "num_predict": 200}
SUBTITLE_BAND = (0.12, 0.80, 0.76, 0.18)   # x, y, width, height of the frame where subtitles are, as shares
SUBTITLE_W = 1280                          # a strip's width on the sheet (about the band's own size at 1080p)
SUBTITLE_EVERY_S = 1.0                     # subtitles stay up for seconds, so one look a second reads them all
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
    # The six safety questions must all be answered. A SEEN question left out counts as not seen (its label is
    # then dropped, never kept), so a v2 answer still reads; one answered with anything but true/false is unusable.
    bad = [k for k in WHAT if not isinstance(a.get(k), bool)]
    bad += [k for k in SEEN if k in a and not isinstance(a[k], bool)]
    if bad:
        raise ValueError(f"not true/false: {', '.join(bad)}")
    note = " ".join(str(a.get("note", "")).split())
    if not note:
        raise ValueError("empty note")
    return {"note": note, **{k: a[k] for k in WHAT}, **{k: a.get(k, False) for k in SEEN}}


def load_prompt(game, version=PROMPT_VERSION):
    return index.load_prompt(game, version)


def profanity(text):
    """The swear words in a text (PROFANITY), lower case, in order; [] for none."""
    return [m.group(0).lower() for m in PROFANITY.finditer(text or "")]


def parse_subtitles(content):
    """The model's reading of a subtitle sheet as [text of strip 1, ..., strip 4]; ValueError if it isn't that."""
    try:
        a = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"reply is not JSON ({e})") from None
    if not isinstance(a, dict) or not all(isinstance(a.get(f"strip_{k}"), str) for k in range(1, 5)):
        raise ValueError("reply needs strip_1 to strip_4 as text")
    return [" ".join(a[f"strip_{k}"].split()) for k in range(1, 5)]


def sheet_times(a, b, every):
    """The frame times of a..b, one every `every` seconds, in sheets of 4: [(first, end, [times])]."""
    times, t = [], a
    while t < b - 1e-6:
        times.append(round(t, 3))
        t += every
    return [(ts[0], round(min(b, ts[-1] + every), 3), ts) for ts in (times[k:k + 4] for k in range(0, len(times), 4))]


def make_sheets(path, a, b, every, out_dir, width=SHEET_W, subtitles=False):
    """JPEG sheets of a..b of a recording (2x2, a frame every `every` s, in reading order; the last one may end in
    black): [(from_s, to_s, file)]. One ffmpeg run for the whole stretch. subtitles=True: each frame is cut to its
    subtitle band (SUBTITLE_BAND, at full size) and 4 of them are stacked, for reading the words."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = sheet_times(a, b, every)
    stem = f"{Path(path).stem}_{a:.1f}_{b:.1f}_{every:g}{'_sub' if subtitles else ''}".replace(".", "_")
    info = clip.probe(shutil.which("ffprobe"), Path(path))
    conv = "in_color_matrix=bt709:in_range=tv:" if clip.needs_range_fix(info["video_codec"], info["color_range"]) else ""
    if subtitles:
        x, y, w, h = SUBTITLE_BAND
        shape = f"crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale={SUBTITLE_W}:-2:{conv}out_color_matrix=bt601:out_range=pc,tile=1x4"
    else:
        shape = f"scale={width}:-2:{conv}out_color_matrix=bt601:out_range=pc,tile=2x2"
    graph = f"select='gte(t-{a:.3f},selected_n*{every:g})',{shape}"
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a + 0.05:.3f}", "-copyts",
                           "-i", str(path), "-vf", graph, "-fps_mode", "passthrough", "-q:v", "4",
                           "-start_number", "0", str(out_dir / f"{stem}_%03d.jpg")], capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"ffmpeg could not make safety sheets of {Path(path).name}: {done.stderr.strip()[-300:]}")
    files = sorted(out_dir.glob(f"{stem}_*.jpg"))
    return [(f0, f1, f) for (f0, f1, _), f in zip(plan, files)]


class MemoryError_(Exception):
    """The safety checks stopped because memory ran out even after unloading the model; the answers so far are
    cached."""


class Checker:
    """The safety question, asked through Ollama with the index's own call (index.ask_model) and cached like the
    index's answers. ask and sheets are swapped for stand-ins by the tests."""

    def __init__(self, game, cache_dir, model=index.MODEL, ask=index.ask_model, sheets=make_sheets, info=None,
                 log=print, version=PROMPT_VERSION, ram=None, unload=None):
        self.version = version
        self.prompt, self.sha = load_prompt(game, version)
        self.sub_prompt, self.sub_sha = load_prompt(game, SUBTITLE_VERSION)
        self.cache_dir, self.model, self.ask, self.sheets, self.log = Path(cache_dir), model, ask, sheets, log
        self.info = info if info is not None else vlm.check_ollama(model)
        self.calls, self.cached, self.seconds = 0, 0, 0.0
        # The index's memory guard (launch-fixes): Ollama's memory grows with every picture, and on launch night the
        # safety stage, which had no guard, was stopped by Claude Code at 3.8 GB free with Ollama holding 8.5 GB.
        self.ram, self.unload = ram or index.free_ram_gb, unload or vlm.unload
        self.since_reload, self.reloads = 0, 0

    def before_call(self):
        """Unload the model when free memory is low (the next call loads it again), as index.label_frames does;
        stop with MemoryError_ when even that leaves too little."""
        free = self.ram()
        if free is not None and free < index.RELOAD_BELOW_GB and self.since_reload >= index.RELOAD_MIN_CALLS:
            self.unload(self.model)
            self.reloads, self.since_reload, free = self.reloads + 1, 0, self.ram()
            self.log(f"  memory was low: unloaded the model after {self.calls} safety call(s), "
                     f"{free if free is not None else 0:.1f} GB free now; it loads again")
        if free is not None and free < index.MIN_FREE_RAM_GB:
            raise MemoryError_(f"only {free:.1f} GB of memory free (needs {index.MIN_FREE_RAM_GB:g}) during the safety "
                               "checks. The answers so far are cached: close other programs and run the same command "
                               "again.")
        self.since_reload += 1

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
                self.before_call()
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

    def read(self, path, a, b, work):
        """[(time, words)] for every second of a..b: the subtitle band read by the model ("" where there is no text).
        A sheet with no usable reading gives "<unreadable>" for its seconds, which counts as unsafe (see swearing)."""
        out = []
        for f0, f1, jpg in self.sheets(path, a, b, SUBTITLE_EVERY_S, work, subtitles=True):
            video = Path(path)
            fields = {"video": video.name, "video_bytes": video.stat().st_size if video.exists() else None,
                      "from_ms": round(f0 * 1000), "to_ms": round(f1 * 1000), "every_s": SUBTITLE_EVERY_S,
                      "band": list(SUBTITLE_BAND), "strip_width": SUBTITLE_W, "model": self.model,
                      "model_digest": self.info.get("digest"), "prompt_version": SUBTITLE_VERSION,
                      "prompt_sha256": self.sub_sha, "schema": SUBTITLE_SCHEMA, "options": SUBTITLE_OPTIONS}
            rec = vlm.cache_load(self.cache_dir, fields)
            if rec is None:
                self.before_call()
                rec = self.ask(self.model, self.sub_prompt, jpg, self.info.get("capabilities", []),
                               schema=SUBTITLE_SCHEMA, parse=parse_subtitles, options=SUBTITLE_OPTIONS)
                self.calls += 1
                self.seconds += rec.get("wall_s") or 0.0
                if rec.get("error") is None:
                    vlm.cache_save(self.cache_dir, fields, rec)
            else:
                self.cached += 1
            times = [t for t0, t1, ts in sheet_times(f0, f1, SUBTITLE_EVERY_S) for t in ts]
            texts = rec.get("answer") or ["<unreadable>"] * len(times)
            out += list(zip(times, texts))
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


def swearing_in(checker, path, spans, work):
    """Swear words in the subtitles of these stretches, read once a second: [(from_s, to_s, what)]. Unreadable
    subtitles count as a hit: unchecked words are not safe words."""
    hits = []
    for a, b in spans:
        for t, text in checker.read(path, a, b, work):
            if text == "<unreadable>":
                hits.append((t, t + SUBTITLE_EVERY_S, "subtitles the model could not read"))
            elif profanity(text):
                hits.append((t, t + SUBTITLE_EVERY_S, f"swearing in the subtitles (\"{', '.join(profanity(text))}\")"))
    return hits


def bad_start(checker, rec, path, spans, work):
    """What may not be in a Short's first 7 s: blood or gore (the model, the index's lines) or swearing in the
    subtitles. [(from_s, to_s, what)]."""
    return gore_in(checker, rec, path, spans, work) + swearing_in(checker, path, spans, work)


def fmt(hits):
    return [f"{what} at {library.hms(a)}-{library.hms(b)}" for a, b, what in hits]


def check_moment(m, rec, folder, checker):
    """(passes, reasons) for one moment, and m["safety"] with what was found. Each type's footage (and the loop
    ending's lead for NARRATED) is asked about; a blocking answer anywhere in it drops that type; gore or swearing in
    the subtitles of its first 7 s moves its start 7 s later once (or drops it); swearing later is a PROFANITY flag. The types that pass go into m["safe_options"] as
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
        gore = bad_start(checker, rec, rec["path"], first_seconds(start), work)
        # Once, 7 s later: the end moves too where the clean stretch allows, so it stays long enough, and an action
        # clip must still hold its moment. The footage it gains at the end is asked about as well.
        later, longer = round(start + FIRST_S, 2), round(max(end, start + FIRST_S + need), 2)
        if (gore and longer <= m["span"][1] - footage_short.GUARD_S + 1e-6
                and (k != "action" or later < opts["anchor_s"])):
            extra = found(checker.look(rec["path"], end, longer, every, work), BLOCKING) if longer > end else []
            if not extra:
                start, end, moved = later, longer, True
                gore = bad_start(checker, rec, rec["path"], first_seconds(start), work)
        if gore:
            report["types"][k] = {"dropped": fmt(gore), "why": "blood, gore or swearing in the first 7 s"}
            reasons.append(f"{k}: blood, gore or swearing in the first 7 s ({'; '.join(fmt(gore))})")
            continue
        safe[k] = [start, end]
        report["types"][k] = {"window": [start, end], "moved_for_gore": moved}
    for a, b, what in found(sheets, ("subtitles",)):
        report["flags"].append(f"SUBTITLES at {library.hms(a)}-{library.hms(b)}: check the words (mission dialogue "
                               "can swear)")
    for a, b, what in found(sheets, ("blood_or_gore",)):
        report["flags"].append(f"BLOOD at {library.hms(a)}-{library.hms(b)} (after the first 7 s): check it")
    # Swearing anywhere else: the subtitle band is read wherever the safety question saw subtitles.
    if safe:
        lo2, hi2 = min(w[0] for w in safe.values()), max(w[1] for w in safe.values())
        spans = [(max(lo2, s["from_s"]), min(hi2, s["to_s"])) for s in sheets
                 if (s.get("answer") or {}).get("subtitles") and s["from_s"] < hi2 and lo2 < s["to_s"]]
        for a, b, what in swearing_in(checker, rec["path"], spans, work):
            report["flags"].append(f"PROFANITY at {library.hms(a)}: {what}")
    m["safety"], m["safe_options"] = report, safe
    m["verified"] = seen_spans(sheets)
    return bool(safe), reasons


def seen_spans(sheets):
    """{what: [[from_s, to_s], ...]} for each SEEN thing the model saw, from the safety sheets (launch-fixes)."""
    out = {}
    for s in sheets:
        a = s.get("answer") or {}
        for k in SEEN:
            if a.get(k):
                out.setdefault(k, []).append([s["from_s"], s["to_s"]])
    return out


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
