"""Marked mode for meme Shorts (Nov 28): the owner marks the funny moments while playing, and the pipeline only edits.

Why: sample 1 of Nov 27 was picked by the automatic finder from the swearing in its dialogue, not from anything funny on
screen, and the owner called it pointless. So the finder is a suggestion list only; the moments come from the owner:

  (a) PS5 "Save recent gameplay" clips (each ends just after the moment), copied to C:\\AI\\gta\\marked\\
  (b) C:\\AI\\gta\\marks.txt, one line a moment: "<clock time IST> <a few words>", e.g. "11:47 bear attacks protesters"

  .\\.venv-tts\\Scripts\\python.exe meme_short.py marked

Each new clip or line is placed (a clip: its last 25 s; a mark: the recording in C:\\AI\\gta\\input whose time covers
it, 20 s before to 5 s after it), its punch found (the biggest jump of sound and picture there), the lines heard around
it read (Whisper), cut to meme rules 1-8 (a 7-12 s gag, the punch at 0:02-0:06, a hard cut 0.5-1.5 s after the last
line around it, a first second that passes rule 2 measured), captioned from what the frames show (the vision model;
never from swear words) and rendered with every kept check (meme_short.render). Everything, made or not, is listed in
C:\\AI\\gta\\shorts\\marked-<date>\\picks.md with the reason; the record (C:\\AI\\gta\\marked\\marked_record.json) keeps
what is done, so nothing is rendered twice.

A recording's file name is the time it ENDED (the PS5 saves it then), in IST: measured on the owner's files (Nov 28),
read as the start three back-to-back pairs would overlap by 450-600 s with different pictures; read as the end every gap
is 0 s or more. Nothing here indexes a recording, writes the bank, the index, _used_footage.json or posted_topics.json.
"""
import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import index
import library
import meme_short
import safety

ROOT = meme_short.ROOT
MARKED_DIR, MARKS_FILE, INPUT_DIR = ROOT / "marked", ROOT / "marks.txt", ROOT / "input"
RECORD_NAME = "marked_record.json"
VIDEO_EXTS = (".webm", ".mp4", ".mkv", ".mov")
STAMP = re.compile(r"_(\d{14})$")
BEFORE_S, AFTER_S, MINUTE_S = 20.0, 5.0, 59.0  # a mark's window; a mark to the minute covers the minute it names
CLIP_TAIL_S = 25.0                              # a saved clip ends just after the moment: look in its last 25 s
MIN_INSIDE_S = 3.0                              # a recording places a mark when this much of the window is in it
MEASURE_BEFORE_S, MEASURE_AFTER_S = 7.0, 12.0   # measured around the window: room for the cut's start and its end
HEAR_BEFORE_S, HEAR_AFTER_S = 8.0, 11.0         # Whisper hears this much around the punch
LINE_GAP_S = 0.7                                # words further apart than this are two lines
REACT_FROM_S, REACT_TO_S = -1.0, 4.0            # a reaction line starts this near the punch...
CHAIN_GAP_S = 1.5                               # ...or within this of the end of the line before it (one reaction)
NEXT_LINE_GAP_S = 0.05                          # the hard cut stays this far before the next line, when it can
PUNCH_T_BEST = 3.0                              # the punch is put this far in when the rules leave a choice
END_TRIES = (1.0, 0.8, 1.2, 0.6, 1.4, 0.5, 1.5)  # the hard cut after the last line, best first (rule 7: 0.5-1.5 s)
# When no plain cut starts alive (with no line after it, rules 1, 2 and 7 put a gag's punch at 5.5-6 s, behind a long
# lead-in), rule 6's one replay lets it come 2.5-6 s in: the main part stops 0.5-1.5 s after the punch, then the punch
# again from 1 s before it, 3 s at 0.5x, so the cut comes 1 s after the replay's own punch (rule 7).
REPLAY = {"type": "replay", "back_s": 1.0, "for_s": 3.0}
REPLAY_AFTER_TRIES = (1.0, 1.2, 0.8, 1.5, 0.5)
SHEET_AT = (-1.5, -0.5, 0.3, 1.2)               # the caption sheet's frames, against the punch
CAPTION_PROMPT = "meme_caption_v1"
CAPTION_SCHEMA = {"type": "object", "properties": {"seen": {"type": "string"},
                                                   "captions": {"type": "array", "items": {"type": "string"}},
                                                   "title": {"type": "string"}},
                  "required": ["seen", "captions", "title"]}
CAPTION_OPTIONS = {"temperature": 0.6, "seed": 42, "num_ctx": 4096, "num_predict": 220}
STOCK = ("Nobody saw that coming", "Just another day for Jason", "That escalated quickly")
SWEARISH = re.compile(r"(?i)(?<![a-z])(f+k+\w*|fkn\w*|sh+t+\w*|b+tch\w*|d+mn\w*|a+zz\w*)(?![a-z])")
DONE = ("rendered", "same")                     # never tried again; "failed" and "waiting" are, every run

MARK_LINE = re.compile(r"^(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?(?:\s*([ap])\.?\s*m\.?)?(?:\s+(.*))?$", re.I)
DATE_LINE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


@dataclass
class Paths:
    marks: Path = MARKS_FILE
    clips: Path = MARKED_DIR
    record: Path = MARKED_DIR / RECORD_NAME
    input: Path = INPUT_DIR
    out: Path = meme_short.SHORTS


# --- Times ---

def stamp_of(name):
    """The time in a PS5 file name (..._YYYYMMDDHHMMSS), or None."""
    m = STAMP.search(Path(name).stem if Path(name).suffix.lower() in VIDEO_EXTS else str(name))
    return datetime.strptime(m.group(1), "%Y%m%d%H%M%S") if m else None


def span_of(name, duration):
    """(start, end) of a recording: its file name's time is when it ended."""
    end = stamp_of(name)
    return end - timedelta(seconds=float(duration)), end


def parse_marks(text):
    """(marks, unreadable) of marks.txt: a mark is {key, line, line_no, date, h, m, s, precision, twelve, words};
    unreadable is [(line_no, text)]. A line with only a date dates the lines under it; # and blank lines are skipped."""
    marks, bad, seen, day = [], [], {}, None
    for n, raw in enumerate(text.splitlines(), 1):
        line = " ".join(raw.split())
        if not line or line.startswith("#"):
            continue
        d = DATE_LINE.match(line)
        if d:
            try:
                day = date(int(d.group(1)), int(d.group(2)), int(d.group(3)))
            except ValueError:
                bad.append((n, line))
            continue
        m = MARK_LINE.match(line)
        h, mi, s = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else (99, 0, 0)
        ampm = (m.group(4) or "").lower() if m else ""
        if not m or mi > 59 or s > 59 or h > (12 if ampm else 23) or (ampm and h == 0):
            bad.append((n, line))
            continue
        if ampm:
            h = h % 12 + (12 if ampm == "p" else 0)
        base = f"{day.isoformat()} {line}" if day else line
        seen[base] = seen.get(base, 0) + 1
        marks.append({"key": base + (f" #{seen[base]}" if seen[base] > 1 else ""), "line": line, "line_no": n,
                      "date": day, "h": h, "m": mi, "s": s, "precision": "second" if m.group(3) else "minute",
                      "twelve": not ampm and (1 <= h <= 12), "words": (m.group(5) or "").strip()})
    return marks, bad


def candidates(mark, saved_at):
    """The moments a mark can mean, best first: on its date, or else the latest moment with that clock time at or before
    marks.txt was saved; a time with no am/pm before 13:00 can also be the other half of the day (tried second)."""
    hours = [mark["h"]] + ([(mark["h"] + 12) % 24 if mark["h"] < 12 else 0] if mark["twelve"] else [])
    out = []
    for h in hours:
        if mark["date"]:
            t = datetime.combine(mark["date"], time(h, mark["m"], mark["s"]))
        else:
            t = saved_at.replace(hour=h, minute=mark["m"], second=mark["s"], microsecond=0)
            if t > saved_at:
                t -= timedelta(days=1)
        out.append(t)
    return out


def clock_window(t, precision):
    """(from, to) of a mark in clock time: 20 s before to 5 s after it (a minute mark: after the end of its minute)."""
    return t - timedelta(seconds=BEFORE_S), t + timedelta(seconds=AFTER_S + (MINUTE_S if precision == "minute" else 0))


def place(times, recordings, precision):
    """The recording that covers a mark: {recording, at_s, window (seconds of the recording), clock, note}, or
    {reason}. recordings: [{name, path, duration, start, end}]. The first of `times` a recording covers is used."""
    for i, t in enumerate(times):
        c0, c1 = clock_window(t, precision)
        best = None
        for rec in recordings:
            inside = (min(c1, rec["end"]) - max(c0, rec["start"])).total_seconds()
            if inside >= MIN_INSIDE_S and (best is None or inside > best[0]):
                best = (inside, rec)
        if best:
            rec = best[1]
            at = (t - rec["start"]).total_seconds()
            a = max(0.0, (c0 - rec["start"]).total_seconds())
            b = min(rec["duration"], (c1 - rec["start"]).total_seconds())
            note = ""
            if i:
                half = "afternoon" if t.hour >= 12 else "night (after midnight)"
                note = (f"read as {t:%H:%M} (the {half}): no recording covers {times[0]:%H:%M} on "
                        f"{times[0]:%Y-%m-%d}")
            return {"recording": rec, "at_s": round(at, 2), "window": (round(a, 2), round(b, 2)), "clock": t,
                    "note": note}
    t = times[0]
    near = sorted(recordings, key=lambda r: min(abs((t - r["start"]).total_seconds()),
                                                abs((t - r["end"]).total_seconds())))
    tried = " or ".join(f"{x:%H:%M:%S}" for x in times)
    where = (f" (the nearest: {near[0]['name']}, {near[0]['start']:%Y-%m-%d %H:%M:%S}-{near[0]['end']:%H:%M:%S})"
             if near else " (no recordings found)")
    return {"reason": f"no recording covers {tried} on {t:%Y-%m-%d}{where}"}


def clip_window(duration):
    """Where a saved clip's moment is: its last CLIP_TAIL_S seconds."""
    return (round(max(0.0, duration - CLIP_TAIL_S), 2), round(duration, 2))


# --- The record of what is done ---

def read_record(path):
    """The record ({"items": [...]}), empty when the file isn't there; StateError when it can't be read (a damaged
    record is never read as empty: that would make everything again)."""
    return index.read_record(path, "items") or {"items": []}


def write_record(path, rec):
    index.write_json(Path(path), rec)


def entry(rec, key):
    return next((e for e in rec["items"] if e.get("key") == key), None)


def set_entry(rec, key, **fields):
    e = entry(rec, key)
    if e is None:
        e = {"key": key}
        rec["items"].append(e)
    e.update(fields)
    return e


def todo(keys, rec):
    """The keys still to do: everything but what is rendered or found to be the same moment as a clip."""
    return [k for k in keys if (entry(rec, k) or {}).get("status") not in DONE]


def times_for(mark, saved_at, rec):
    """A mark's moments, worked out the first time it is seen and kept in the record (saving marks.txt again later
    doesn't move an undated mark to another day)."""
    e = entry(rec, mark["key"])
    if e and e.get("times"):
        return [datetime.fromisoformat(x) for x in e["times"]]
    got = candidates(mark, saved_at)
    set_entry(rec, mark["key"], times=[t.isoformat() for t in got])
    return got


# --- The punch and the cut ---

def robust_z(values, floor):
    med = sorted(values)[len(values) // 2]
    mad = sorted(abs(v - med) for v in values)[len(values) // 2]
    scale = max(1.4826 * mad, floor)
    return [(v - med) / scale for v in values]


def find_punch(picture, sound, t0, window):
    """The punch in window (seconds of the recording; the measurements start at t0, one every STEP_S): where sound and
    picture jump most above their own usual level (robust z, the two added), at the jump's start."""
    step = meme_short.STEP_S
    n = min(len(picture), len(sound))
    if not n:
        return {"at_s": None, "reason": "nothing could be measured there"}
    zp, zs = robust_z(picture[:n], 0.5), robust_z(sound[:n], 1.0)
    raw = [max(0.0, a) + max(0.0, b) for a, b in zip(zp, zs)]
    smooth = [sum(raw[max(0, i - 1):i + 2]) / len(raw[max(0, i - 1):i + 2]) for i in range(n)]
    i0 = max(0, math.ceil((window[0] - t0) / step - 1e-6))
    i1 = min(n - 1, math.floor((window[1] - t0) / step + 1e-6))
    if i1 < i0:
        return {"at_s": None, "reason": "the window is outside what was measured"}
    best = max(range(i0, i1 + 1), key=lambda i: (smooth[i], -i))
    top = max(range(max(i0, best - 1), min(i1, best + 1) + 1), key=lambda i: (raw[i], -i))
    j = top
    while j - 1 >= i0 and top - (j - 1) <= 10 and raw[j - 1] >= 0.5 * raw[top]:
        j -= 1
    return {"at_s": round(t0 + j * step, 2), "strength": round(smooth[best], 2), "sound_z": round(zs[top], 2),
            "picture_z": round(zp[top], 2)}


def lines_of(words, gap=LINE_GAP_S):
    """Whisper's words [(start, end, word)] as lines [{from_s, to_s, text}]: a gap over `gap` s starts a new line."""
    out = []
    for a, b, w in sorted(words):
        if out and a - out[-1]["to_s"] <= gap:
            out[-1]["to_s"] = round(b, 2)
            out[-1]["text"] += " " + w
        else:
            out.append({"from_s": round(a, 2), "to_s": round(b, 2), "text": w})
    return out


def plan_cut(punch, lines, picture, sound, t0, duration):
    """The cut round a punch, to meme rules 1, 2 and 7: {from_s, to_s, punchline_s, reaction_end_s, motion_s, heard,
    first_second, left_out} or {reason}. The anchor is the end of the last line heard round the punch that fits (a gag
    is at most 12 s with the punch 2 s in); the hard cut comes 0.5-1.5 s after it; the start is the one 2-6 s before the
    punch whose first second passes rule 2 measured, nearest PUNCH_T_BEST."""
    step = meme_short.STEP_S
    lo_g, hi_g = meme_short.KINDS["gag"]
    p = round(float(punch), 2)
    longest = hi_g - meme_short.PUNCH_AT[0] - meme_short.END_AFTER[0]
    kept, left_out = [], []
    for ln in sorted(lines, key=lambda ln: ln["from_s"]):
        if ln["from_s"] - p < REACT_FROM_S:
            continue
        if not (ln["from_s"] - p <= REACT_TO_S or (kept and ln["from_s"] - kept[-1]["to_s"] <= CHAIN_GAP_S)):
            break
        if ln["to_s"] - p > longest + 1e-6:
            left_out.append(ln)
            break
        kept.append(ln)
    anchors = [round(max(p, ln["to_s"]), 2) for ln in reversed(kept)] + [p]

    def ends(anchor, tries):
        """The hard cuts after anchor, those that don't land inside the next line first."""
        nxt = min((ln["from_s"] for ln in lines if ln["from_s"] > anchor + 1e-6), default=None)
        clear = [a for a in tries if nxt is None or anchor + a <= nxt - NEXT_LINE_GAP_S + 1e-6]
        return clear + [a for a in tries if a not in clear]
    best_fail = None

    def starts(lo, hi, to, edit=None):
        """(alive, distance from PUNCH_T_BEST, start, measured, problems) of every start lo..hi of a cut ending at to
        (and its replay, which counts in the rest of the Short)."""
        tried, k = [], math.ceil((lo - t0) / step - 1e-6)
        if edit:
            r0 = round((p - edit["back_s"] - t0) / step)
            r1 = r0 + round(edit["for_s"] * meme_short.REPLAY_SPEED / step)
        while t0 + k * step <= hi + 1e-6:
            f = round(t0 + k * step, 2)
            pic, snd = picture[k:round((to - t0) / step)], sound[k:round((to - t0) / step)]
            if edit:
                pic, snd = meme_short.with_replay(pic, snd, picture[r0:r1], sound[r0:r1])
            got, bad = meme_short.start_verdict(pic, snd)
            tried.append((not bad, abs(p - f - PUNCH_T_BEST), f, got, bad))
            k += 1
        return tried

    def made(tried, to, anchor, edit=None):
        _, _, f, got, _ = min((x for x in tried if x[0]), key=lambda x: (x[1], -x[2]))
        at = got["picture_at_s"] if got["alive_by"] in ("picture", "picture and sound") else got["sound_at_s"]
        out_lines = [x for x in kept if x["to_s"] > anchor] + left_out
        return {"from_s": f, "to_s": to, "punchline_s": p, "reaction_end_s": anchor if anchor > p and not edit else None,
                "motion_s": round(f + at, 2), "heard": [dict(ln) for ln in lines if ln["to_s"] > f and ln["from_s"] < to],
                "first_second": got, "edit": edit,
                "left_out": [f"left out \"{meme_short.censor(ln['text'])}\" ({ln['from_s']:.1f}-{ln['to_s']:.1f} s): "
                             "too long to fit" for ln in out_lines]
                + (["a 0.5x replay of the punch: no plain 7-12 s cut starts alive (rule 2 measured)"] if edit else [])}

    for anchor in dict.fromkeys(anchors):
        for after in ends(anchor, END_TRIES):
            to = round(anchor + after, 2)
            if to > duration + 1e-6:
                continue
            lo = max(p - meme_short.PUNCH_AT[1], to - hi_g, 0.0, t0)
            hi = min(p - meme_short.PUNCH_AT[0], to - lo_g)
            if lo > hi + 1e-6:
                continue
            tried = starts(lo, hi, to)
            if any(x[0] for x in tried):
                return made(tried, to, anchor)
            if tried and best_fail is None:
                best_fail = min(tried, key=lambda x: x[1])
    for after in ends(p, REPLAY_AFTER_TRIES):
        to = round(p + after, 2)
        if to > duration + 1e-6:
            continue
        lo = max(p - meme_short.PUNCH_AT[1], to + REPLAY["for_s"] - hi_g, 0.0, t0)
        hi = min(p - meme_short.PUNCH_AT[0], to + REPLAY["for_s"] - lo_g)
        if lo > hi + 1e-6:
            continue
        tried = starts(lo, hi, to, REPLAY)
        if any(x[0] for x in tried):
            return made(tried, to, p, dict(REPLAY))
    if best_fail:
        return {"reason": f"every start 2-6 s before the punch at {library.hms(p)} ({p:.1f} s) is still and quiet, with "
                          "or without a replay: "
                          + best_fail[4][0], "first_second": best_fail[3]}
    return {"reason": f"no 7-12 s cut fits round the punch at {library.hms(p)} ({p:.1f} s): the footage starts less than "
                      f"2 s before it or ends less than 0.5 s after it"}


# --- Captions from what is seen ---

def pick_captions(answer):
    """(captions, star, title, notes) from the vision model's answer {seen, captions, title}: a caption is kept only
    when it keeps rule 3 and has no swear word (written censored or not); the first kept one is starred; fewer than 3
    are filled with stock captions (and the notes say so); a title that breaks rule 8 becomes the starred caption."""
    caps, notes = [], []
    for c in (answer or {}).get("captions") or []:
        c = " ".join(str(c).split()).strip(" \"'\u201c\u201d")
        if not c or c in caps:
            continue
        if meme_short.censor(c) != c or SWEARISH.search(c):
            notes.append(f"dropped \"{meme_short.censor(c)}\": a swear word")
            continue
        bad = meme_short.caption_problems(c)
        if bad:
            notes.append(f"dropped \"{c}\": {bad[0].replace('rule 3: ', '')}")
            continue
        caps.append(c)
    kept = len(caps)
    caps = caps[:3]
    for c in STOCK:
        if len(caps) >= 3:
            break
        if c not in caps:
            caps.append(c)
    if kept < 3:
        notes.append(f"the model gave {kept} usable caption(s); {3 - kept} filled in with stock ones: write your own")
    title = " ".join(str((answer or {}).get("title") or "").split()).strip(" \"'")
    if not title or meme_short.title_problems(title) or meme_short.censor(title) != title or SWEARISH.search(title):
        notes.append(f"title \"{title}\" not used: {(meme_short.title_problems(title) or ['a swear word'])[0]}"
                     if title else "no title from the model")
        title = caps[0]
    return caps, 0, title, notes


def parse_caption(content):
    try:
        a = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"reply is not JSON ({e})") from None
    if not isinstance(a, dict) or not isinstance(a.get("captions"), list) or not a["captions"]:
        raise ValueError("no captions list")
    return {"seen": " ".join(str(a.get("seen", "")).split()), "captions": [str(c) for c in a["captions"]],
            "title": str(a.get("title", ""))}


# --- The real tools (the checks use stand-ins) ---

class Tools:
    """ffprobe, the measurements, Whisper, the vision model and the render, as production runs them."""

    def __init__(self, log=print):
        self.log, self.info = log, None

    def probe(self, path):
        info = meme_short.ex.probe(path)
        return float(info["format"]["duration"])

    def measure(self, path, a, seconds):
        return meme_short.start_series(path, a, seconds)

    def hear(self, path, a, b):
        import speech
        import tempfile
        if not speech.available():
            raise meme_short.MemeError("marked mode hears the lines with Whisper: run it with .venv-tts\\Scripts\\python.exe")
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "window.wav"
            meme_short.ex.ffmpeg(["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", path, "-vn", "-ac", "1", "-ar", "16000",
                                  "-c:a", "pcm_s16le", wav])
            return [(a + t0, a + t1, w) for t0, t1, w in speech.words(wav)]

    def sheet(self, path, punch, out):
        """A 2x2 sheet (1280x720) of the frames at SHEET_AT round the punch."""
        inputs, scale = [], []
        for i, d in enumerate(SHEET_AT):
            inputs += ["-ss", f"{max(0.0, punch + d):.3f}", "-i", str(path)]
            scale.append(f"[{i}:v]scale=640:360,setsar=1[f{i}]")
        meme_short.ex.ffmpeg([*inputs, "-filter_complex", ";".join(scale) + ";[f0][f1][f2][f3]xstack=inputs=4:"
                              "layout=0_0|w0_0|0_h0|w0_h0[s]", "-map", "[s]", "-frames:v", "1", "-q:v", "3", out])
        return out

    def caption(self, path, punch, lines, work):
        import vlm
        if self.info is None:
            try:
                self.info = vlm.check_ollama(index.MODEL)
            except vlm.VlmError as e:
                raise meme_short.MemeError(f"{e}. Open the Ollama app and run the same command again (what is done "
                                           "stays done)") from None
        work = Path(work)
        work.mkdir(parents=True, exist_ok=True)
        jpg = self.sheet(path, punch, work / "caption_sheet.jpg")
        heard = [meme_short.censor(ln["text"]) for ln in lines if abs(ln["from_s"] - punch) <= 6.0]
        prompt = Path(__file__).with_name("prompts").joinpath(f"{CAPTION_PROMPT}.txt").read_text(encoding="utf-8")
        prompt = prompt.replace("{GAME}", "GTA 6").replace(
            "{HEARD}", ("Heard around it (for context only): " + " / ".join(f'"{h}"' for h in heard)) if heard
            else "No dialogue is heard around it.")
        rec = index.ask_model(index.MODEL, prompt, jpg, self.info.get("capabilities", []), schema=CAPTION_SCHEMA,
                              parse=parse_caption, options=CAPTION_OPTIONS)
        return rec.get("answer") or {"seen": "", "captions": [], "title": "", "error": rec.get("error")}

    def index_of(self, path):
        """The library's index of this file when there is one for it; else a bare one (not indexed: the safety
        question alone checks it)."""
        stem = Path(path).stem
        idx = index.read_json(index.LIBRARY / stem / "index.json")
        if idx and Path(idx.get("video", {}).get("path", "")).resolve() == Path(path).resolve():
            return idx
        return {"video": {"stem": stem, "path": str(path)}, "profile": {"name": "gta6" if "VI_" in stem else "gta5"},
                "sheets": [], "segments": None, "every": 6.0}

    def render(self, spec, folder, source, idx):
        return meme_short.render(spec, folder, source=source, checks="all", idx=idx, log=self.log)

    def unload(self):
        import vlm
        if self.info is not None:
            vlm.unload(index.MODEL)


# --- One run ---

def videos_in(folder):
    folder = Path(folder)
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS) if folder.is_dir() else []


def recordings(paths_list, tools):
    out = []
    for p in paths_list:
        if stamp_of(p.name) is None:
            continue
        d = tools.probe(p)
        start, end = span_of(p.name, d)
        out.append({"name": p.name, "path": str(p), "duration": d, "start": start, "end": end})
    return out


def next_number(day_dir):
    nums = [int(m.group(1)) for p in Path(day_dir).glob("*") if p.is_dir() for m in [re.match(r"^(\d+)-", p.name)] if m]
    return max(nums, default=0) + 1


def run(paths, now, tools, log=print):
    """Every new marked moment, cut, captioned and rendered, and picks.md written. Returns the counts."""
    today = now.date()
    day_dir = Path(paths.out) / f"marked-{today.isoformat()}"
    rec = read_record(paths.record)
    counts = {"rendered": 0, "failed": 0, "waiting": 0, "same": 0, "unreadable": 0}
    stamp = now.isoformat(timespec="seconds")

    def done(key, status, **fields):
        counts[status] += 1
        set_entry(rec, key, status=status, day=today.isoformat(), last_try=stamp, **fields)
        write_record(paths.record, rec)

    # The saved clips
    items = []
    clip_files = videos_in(paths.clips)
    clips = []
    for p in clip_files:
        key = f"clip {p.name} {p.stat().st_size}"
        d = tools.probe(p)
        end = stamp_of(p.name)
        clips.append({"key": key, "name": p.name, "clock": (end - timedelta(seconds=min(d, CLIP_TAIL_S)), end)
                      if end else None})
        if key not in todo([key], rec):
            continue
        items.append({"key": key, "kind": "clip", "source": f"your saved clip {p.name}", "path": str(p), "duration": d,
                      "window": clip_window(d), "note": ""})

    # The marks
    unreadable = []
    if Path(paths.marks).is_file():
        saved_at = datetime.fromtimestamp(Path(paths.marks).stat().st_mtime)
        marks, unreadable = parse_marks(Path(paths.marks).read_text(encoding="utf-8-sig", errors="replace"))
        recs = None
        for m in marks:
            if m["key"] not in todo([m["key"]], rec):
                continue
            times = times_for(m, saved_at, rec)
            c0, c1 = clock_window(times[0], m["precision"])
            same = next((c for c in clips if c["clock"] and c["clock"][0] < c1 and c0 < c["clock"][1]), None)
            if same:
                done(m["key"], "same", source=f"your mark \"{m['line']}\"", reason=f"the same moment as your saved clip "
                     f"{same['name']} (made from the clip)")
                continue
            if recs is None:
                recs = recordings(videos_in(paths.input) + clip_files, tools)
            at = place(times, recs, m["precision"])
            if at.get("reason"):
                done(m["key"], "waiting", source=f"your mark \"{m['line']}\"", reason=at["reason"] + ". Copy that "
                     "recording to " + str(paths.input) + " and run the same command again")
                continue
            r = at["recording"]
            items.append({"key": m["key"], "kind": "mark", "source": f"your mark \"{m['line']}\"", "path": r["path"],
                          "duration": r["duration"], "window": at["window"], "note": at["note"],
                          "clock": f"{at['clock']:%Y-%m-%d %H:%M:%S}", "at_s": at["at_s"]})
    counts["unreadable"] = len(unreadable)

    # Measure, find the punch, hear the lines, cut
    for it in items:
        a = max(0.0, it["window"][0] - MEASURE_BEFORE_S)
        b = min(it["duration"], it["window"][1] + MEASURE_AFTER_S)
        log(f"  {it['source']}: measuring {library.hms(a)}-{library.hms(b)} of {Path(it['path']).name}")
        pic, snd = tools.measure(it["path"], a, b - a)
        punch = find_punch(pic, snd, a, it["window"])
        it["punch"] = punch
        if punch.get("at_s") is None:
            it["reason"] = punch["reason"]
            continue
        p = punch["at_s"]
        lines = lines_of(tools.hear(it["path"], max(0.0, p - HEAR_BEFORE_S), min(it["duration"], p + HEAR_AFTER_S)))
        it["lines"] = lines
        plan = plan_cut(p, lines, pic, snd, a, it["duration"])
        if plan.get("reason"):
            it["reason"] = plan["reason"]
        it["plan"] = plan

    # Captions (the vision model), then it leaves memory before anything else runs
    day_dir.mkdir(parents=True, exist_ok=True)
    try:
        for it in items:
            if it.get("reason"):
                continue
            it["answer"] = tools.caption(it["path"], it["punch"]["at_s"], it.get("lines", []),
                                         day_dir / "_work" / re.sub(r"[^A-Za-z0-9]+", "_", it["key"])[:60])
    finally:
        tools.unload()
        shutil.rmtree(day_dir / "_work", ignore_errors=True)

    # Render
    for it in items:
        base = {"source": it["source"], "file": Path(it["path"]).name, "window": list(it["window"]),
                "clock": it.get("clock"), "note": it.get("note", ""), "punch": it.get("punch")}
        if it.get("reason"):
            done(it["key"], "failed", reason=it["reason"], **base)
            continue
        plan = it["plan"]
        caps, star, title, notes = pick_captions(it["answer"])
        stem = Path(it["path"]).stem
        spec = {"recording": stem, "from_s": plan["from_s"], "to_s": plan["to_s"], "kind": "gag",
                "motion_s": plan["motion_s"], "punchline_s": plan["punchline_s"],
                "reaction_end_s": plan["reaction_end_s"], "heard": plan["heard"], "layout": "letterbox",
                "edit": plan.get("edit"),
                "focus": [0.5, 0.5], "captions": caps, "star": star, "title": title,
                "what": (it["answer"] or {}).get("seen", ""), "why": f"marked by the owner: {it['source']}",
                "notes": notes + plan.get("left_out", []) + ([it["note"]] if it.get("note") else [])}
        spec["slug"] = meme_short.slug_of(spec)
        folder = day_dir / f"{next_number(day_dir)}-{spec['slug']}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "spec.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False), encoding="utf-8")
        bad = meme_short.problems(spec)
        if bad:
            done(it["key"], "failed", reason="; ".join(bad), folder=str(folder), **base)
            continue
        try:
            got = tools.render(spec, folder, it["path"], tools.index_of(it["path"]))
        except (meme_short.MemeError, safety.SafetyError, meme_short.ex.ScriptError, subprocess.CalledProcessError) as e:
            done(it["key"], "failed", reason=str(e), folder=str(folder), captions=caps, star=star, title=title, **base)
            continue
        done(it["key"], "rendered", folder=str(folder), short=got["short"], captions=caps, star=star, title=title,
             seen=spec["what"], notes=spec["notes"], flags=got.get("flags", []), from_s=spec["from_s"],
             to_s=spec["to_s"], punchline_s=spec["punchline_s"], heard=[ln["text"] for ln in spec["heard"]],
             seconds=(got.get("measured") or {}).get("duration_s"), **base)
        log(f"  READY {got['short']}")

    page = write_picks(day_dir, rec, unreadable, now, paths)
    counts["page"] = str(page)
    return counts


# --- picks.md ---

def write_picks(day_dir, rec, unreadable, now, paths):
    day = day_dir.name.replace("marked-", "")
    made = [e for e in rec["items"] if e.get("status") == "rendered" and e.get("day") == day]
    other = [e for e in rec["items"] if e.get("status") != "rendered" and e.get("day") == day]
    py = r".\.venv-tts\Scripts\python.exe"
    out = [f"# Your marked moments, {day}", "",
           f"Made by `meme_short.py marked` (last run {now:%H:%M}). Watch each Short with sound: the starred caption is the "
           "one on it. To use another caption or title, change `captions`, `star` or `title` in the Short's `spec.json` and "
           "run the line under it. Nothing is uploaded.", "", f"## Made ({len(made)})", ""]
    if not made:
        out += ["None yet.", ""]
    for n, e in enumerate(made, 1):
        caps = e.get("captions") or []
        out += [f"### {n}. {e.get('title', '')}", "",
                f"- **Short:** `{e.get('short')}` ({e.get('seconds')} s)",
                f"- **From:** {e['source']}" + (f" -> `{e['file']}`" if e.get("clock") else "")
                + (f" at {e['clock']}" if e.get("clock") else "") + f"; looked for the punch in "
                f"{library.hms(e['window'][0])}-{library.hms(e['window'][1])} of the file"
                + (f" ({e['note']})" if e.get("note") else ""),
                f"- **Punch:** {library.hms(e['punchline_s'])} ({e['punchline_s']:.1f} s of the file, the biggest jump of "
                f"sound and picture there); the cut is {e['from_s']:.1f}-{e['to_s']:.1f} s",
                f"- **Seen (vision model):** {e.get('seen') or '-'}",
                f"- **Heard:** " + (" / ".join(f'"{meme_short.censor(h)}"' for h in e.get("heard") or []) or "no line"),
                "- **Captions:** " + " / ".join(("**\\*" + c + "**") if i == e.get("star") else c for i, c in enumerate(caps)),
                f"- **Title:** {e.get('title')}"]
        out += [f"- **Note:** {x}" for x in e.get("notes") or []]
        out += [f"- **Flag:** {x}" for x in e.get("flags") or []]
        out += ["- **Change it:** edit `spec.json`, then", "", "  ```powershell",
                f"  {py} meme_short.py render \"{Path(e['folder']) / 'spec.json'}\" --out \"{e['folder']}\"", "  ```", ""]
    nm = other + [{"source": f"line {n} of marks.txt: \"{t}\"", "status": "unreadable",
                   "reason": "can't read it: write \"<time> <a few words>\", e.g. \"11:47 bear attacks protesters\" or "
                             "\"23:05:30 car flips\" (a line with only a date, 2026-11-28, dates the lines under it)"}
                  for n, t in unreadable]
    out += [f"## Not made ({len(nm)})", ""]
    if not nm:
        out += ["Nothing: every marked moment was made.", ""]
    what = {"failed": "could not be cut or failed a check", "waiting": "waiting for its recording",
            "same": "already made", "unreadable": "not read"}
    for e in nm:
        out.append(f"- {e['source']}: **{what.get(e['status'], e['status'])}**: {e.get('reason', '')}")
    out += ["", "A moment that failed or is waiting is tried again by the next run; one that was made is never made "
            "again (the record: `" + str(paths.record) + "`).", ""]
    day_dir.mkdir(parents=True, exist_ok=True)
    page = day_dir / "picks.md"
    page.write_text("\n".join(out), encoding="utf-8")
    return page


def main(args):
    paths = Paths(marks=Path(args.marks) if args.marks else MARKS_FILE,
                  clips=Path(args.clips) if args.clips else MARKED_DIR,
                  input=Path(args.input) if args.input else INPUT_DIR,
                  out=Path(args.out) if args.out else meme_short.SHORTS)
    paths.record = paths.clips / RECORD_NAME
    paths.clips.mkdir(parents=True, exist_ok=True)
    got = run(paths, datetime.now(), Tools(), log=print)
    print(f"Made {got['rendered']}; failed {got['failed']}; waiting {got['waiting']}; same moment as a clip "
          f"{got['same']}; unreadable lines {got['unreadable']}. The list: {got['page']}")
