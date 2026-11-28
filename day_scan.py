"""A day's new recordings, scanned cheaply for meme and robbery candidates the owner judges (Nov 28).

Why: the automatic finder can't judge "funny" (it picked the GPS crash for the swearing in its dialogue). So this only
finds and ranks candidates and shows them on one page the owner can judge fast; the owner makes the pick.

  .\\.venv\\Scripts\\python.exe day_scan.py measure  STEM [STEM ...]   clip.py's own pass (loudness, picture, HUD stars)
  .\\.venv\\Scripts\\python.exe day_scan.py spikes   STEM [STEM ...]   candidate windows from those series -> scan.json
  .\\.venv-tts\\Scripts\\python.exe day_scan.py hear                  Whisper on each candidate window only
  .\\.venv\\Scripts\\python.exe day_scan.py look                      the vision model on each candidate's punch only
  .\\.venv\\Scripts\\python.exe day_scan.py sheets                    thumbnails for the review page
  .\\.venv\\Scripts\\python.exe day_scan.py overview STEM [STEM ...]   a frame every 10 s, 30 a sheet, to look at by eye
  .\\.venv-tts\\Scripts\\python.exe day_scan.py transcribe STEM ...   Whisper over whole recordings (voice detector, beam 1)
  .\\.venv\\Scripts\\python.exe day_scan.py page                      index.html from judged.json (what was seen)

Cheap passes first: `measure` is the run /launch and the bank make of every recording (launch.run_clip into the bank's
clip_runs, so a later `bank.py build` reuses it and asks nothing again); `spikes` reads its timeline.csv. Whisper and the
vision model then run only around the spikes. Nothing here writes the bank, the index, _used_footage.json or
posted_topics.json; everything goes to the day's review folder (DAY_DIR).
"""
import argparse
import csv
import json
import math
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(r"C:\AI\gta")
INPUT = ROOT / "input"
BANK_RUNS, BANK_LOGS = ROOT / "bank" / "clip_runs", ROOT / "bank" / "logs"
STEP = 0.5                      # clip.py's timeline step
WINDOW_BEFORE, WINDOW_AFTER = 10.0, 12.0   # a spike's window: room for a 7-12 s gag or a 15-24 s dialogue around it
MIN_GAP = 25.0                  # two spikes nearer than this are one candidate
PER_10_MIN = 6                  # at most this many spikes per 10 minutes of footage (the rest are weaker)
EDGE = 3.0                      # nothing in the first or last 3 s (the PS5's own start and end)


def video_of(stem):
    for ext in (".webm", ".mp4"):
        p = INPUT / f"{stem}{ext}"
        if p.is_file():
            return p
    raise SystemExit(f"ERROR: no recording {stem} in {INPUT}")


def day_dir(day):
    return ROOT / "review" / day


def read_json(path, default=None):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else default


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def hms(s):
    s = max(0.0, float(s))
    return f"{int(s // 60)}:{s % 60:04.1f}"


# --- 1. measure: clip.py as /launch runs it ---

def measure(stems, log=print):
    import launch
    out = {}
    for stem in stems:
        t = time.perf_counter()
        run = launch.run_clip(video_of(stem), "gta6", BANK_RUNS, BANK_LOGS)
        out[stem] = str(run) if run else None
        log(f"  {stem}: {'ok' if run else 'FAILED (see ' + str(BANK_LOGS / ('clip_' + stem + '.txt')) + ')'} "
            f"in {time.perf_counter() - t:.0f} s")
    return out


def timeline(stem):
    import bank
    run = bank.newest_clip_run(stem, ROOT / "shorts", ROOT / "bank")
    if run is None:
        return None, []
    with open(Path(run) / "timeline.csv", newline="", encoding="utf-8") as fh:
        return run, list(csv.DictReader(fh))


# --- 2. spikes: candidate windows from the series ---

def star_changes(rows):
    """(t, before, after) where clip.py's star count changes and stays changed for 2 s."""
    out, last = [], 0
    stars = [int(float(r.get("stars") or 0)) for r in rows]
    for i in range(len(stars)):
        if stars[i] != last and all(s == stars[i] for s in stars[i:i + 4]):
            out.append((float(rows[i]["time_s"]), last, stars[i]))
            last = stars[i]
    return out


def spikes(rows, duration, per_10=PER_10_MIN, gap=MIN_GAP):
    """The strongest moments: the sound's and the picture's z-scores added (each cut at 0), smoothed over 1.5 s; local
    peaks at least `gap` apart, the best `per_10` per 10 minutes, the phone left out. [(t, strength, audio_z, visual_z)]"""
    n = len(rows)
    if not n:
        return []
    az = [max(0.0, float(r["audio_z"])) for r in rows]
    vz = [max(0.0, float(r["visual_z"])) for r in rows]
    phone = [r.get("phone") in ("1", "True", "true") for r in rows]
    raw = [a + 0.5 * v for a, v in zip(az, vz)]
    sm = [sum(raw[max(0, i - 1):i + 2]) / len(raw[max(0, i - 1):i + 2]) for i in range(n)]
    order = sorted(range(n), key=lambda i: -sm[i])
    keep = []
    limit = max(2, math.ceil(duration / 600.0 * per_10))
    for i in order:
        j = max(range(max(0, i - 1), min(n, i + 2)), key=lambda k: (raw[k], -k))   # the peak itself, not a neighbour
        t = float(rows[j]["time_s"])
        if t < EDGE or t > duration - EDGE or any(phone[max(0, j - 2):j + 3]) or sm[i] <= 0:
            continue
        if any(abs(t - k[0]) < gap for k in keep):
            continue
        keep.append((t, round(sm[i], 2), round(az[j], 2), round(vz[j], 2)))
        if len(keep) >= limit:
            break
    return sorted(keep)


def spikes_cmd(stems, day, log=print):
    import meme_short
    scan = read_json(day_dir(day) / "scan.json", {"recordings": {}, "candidates": []})
    cands = [c for c in scan["candidates"] if c["stem"] not in stems]
    for stem in stems:
        run, rows = timeline(stem)
        if not rows:
            log(f"  {stem}: no clip.py run; run `day_scan.py measure {stem}` first")
            continue
        duration = float(meme_short.ex.probe(video_of(stem))["format"]["duration"])
        sc = star_changes(rows)
        scan["recordings"][stem] = {"run": str(run), "duration": duration, "star_changes": sc,
                                    "max_stars": max((int(float(r.get("stars") or 0)) for r in rows), default=0)}
        for t, s, a, v in spikes(rows, duration):
            a0, b0 = max(0.0, t - WINDOW_BEFORE), min(duration, t + WINDOW_AFTER)
            cands.append({"id": f"{stem[-6:]}-{int(t):04d}", "stem": stem, "spike_s": t, "strength": s, "audio_z": a,
                          "visual_z": v, "window": [round(a0, 1), round(b0, 1)],
                          "stars_near": [list(x) for x in sc if a0 - 20 <= x[0] <= b0 + 20]})
        log(f"  {stem}: {sum(1 for c in cands if c['stem'] == stem)} spikes, {len(sc)} star changes")
    scan["candidates"] = sorted(cands, key=lambda c: (c["stem"], c["spike_s"]))
    write_json(day_dir(day) / "scan.json", scan)
    return scan


# --- 3. hear: Whisper on each window only ---

def hear_cmd(day, log=print):
    import speech
    import subprocess
    import tempfile
    import meme_marked
    import meme_short
    path = day_dir(day) / "scan.json"
    scan = read_json(path)
    with tempfile.TemporaryDirectory() as tmp:
        for c in scan["candidates"]:
            if "lines" in c:
                continue
            a, b = c["window"]
            wav = Path(tmp) / "w.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i",
                            str(video_of(c["stem"])), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
                           check=True)
            words = [(a + t0, a + t1, w) for t0, t1, w in speech.words(wav)]
            c["lines"] = meme_marked.lines_of(words)
            c["heard"] = " / ".join(meme_short.censor(ln["text"]) for ln in c["lines"])
            write_json(path, scan)
            log(f"  {c['id']}: {c['heard'][:100] or '-'}")
    return scan


def transcribe_cmd(stems, day, log=print):
    """Whisper over each whole recording, cheaply: the voice detector skips the stretches with no speech and the beam is 1
    (speech.words, which checks finished Shorts, keeps beam 5 and no detector). The lines, in the recording's seconds, go
    to transcripts\\<stem>.json. NPC dialogue is the funny candidates' best signal and the spikes miss it."""
    import speech
    tdir = day_dir(day) / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    import meme_marked
    import meme_short
    for stem in stems:
        out = tdir / f"{stem}.json"
        if out.is_file():
            continue
        t = time.perf_counter()
        sound = speech.audio(video_of(stem))
        segments, _ = speech.load().transcribe(sound, language="en", word_timestamps=True, vad_filter=True, beam_size=1,
                                               condition_on_previous_text=False)
        words = [(w.start, w.end, w.word.strip()) for s in segments for w in (s.words or [])]
        lines = meme_marked.lines_of(words)
        for ln in lines:
            ln["text"] = meme_short.censor(ln["text"])
        write_json(out, {"stem": stem, "seconds": round(len(sound) / 16000, 1), "lines": lines,
                         "took_s": round(time.perf_counter() - t, 1)})
        log(f"  {stem}: {len(lines)} lines in {time.perf_counter() - t:.0f} s")


# --- 4. look: the vision model on each punch only ---

LOOK_PROMPT = ("These are 4 frames from 2 seconds of GTA 6 gameplay (left to right, top to bottom). In one sentence, say "
               "what happens. Then answer with JSON: {\"seen\": \"...\", \"kind\": one of \"npc_fight\", "
               "\"npc_dialogue\", \"ragdoll\", \"crash\", \"robbery\", \"driving\", \"walking\", \"menu_or_cutscene\", "
               "\"other\", \"store\": true if the inside of a shop with a counter or display cases is in view}")
LOOK_SCHEMA = {"type": "object", "properties": {"seen": {"type": "string"}, "kind": {"type": "string"},
                                                "store": {"type": "boolean"}}, "required": ["seen", "kind", "store"]}


def look_cmd(day, log=print):
    import index
    import meme_marked
    import vlm
    path = day_dir(day) / "scan.json"
    scan = read_json(path)
    tools = meme_marked.Tools(log)
    info = vlm.check_ollama(index.MODEL)
    work = day_dir(day) / "_look"
    work.mkdir(parents=True, exist_ok=True)

    def parse(content):
        a = json.loads(content)
        return {"seen": str(a.get("seen", "")), "kind": str(a.get("kind", "other")), "store": bool(a.get("store"))}
    try:
        for c in scan["candidates"]:
            if "look" in c:
                continue
            jpg = tools.sheet(video_of(c["stem"]), c["spike_s"], work / f"{c['id']}.jpg")
            rec = index.ask_model(index.MODEL, LOOK_PROMPT, jpg, info.get("capabilities", []), schema=LOOK_SCHEMA,
                                  parse=parse, options={"temperature": 0.0, "seed": 1, "num_ctx": 4096,
                                                        "num_predict": 160})
            c["look"] = rec.get("answer") or {"seen": "", "kind": "other", "store": False, "error": rec.get("error")}
            write_json(path, scan)
            log(f"  {c['id']}: {c['look'].get('kind')} | {c['look'].get('seen', '')[:100]}")
    finally:
        vlm.unload(index.MODEL)
    return scan


# --- 5. sheets: thumbnails for the page ---

def thumbs(video, times, out):
    """A row of small frames (320x180) at the given seconds, side by side, as one JPEG."""
    import subprocess
    inputs, parts = [], []
    for i, t in enumerate(times):
        inputs += ["-ss", f"{max(0.0, t):.3f}", "-i", str(video)]
        parts.append(f"[{i}:v]scale=320:180,setsar=1[f{i}]")
    stack = "".join(f"[f{i}]" for i in range(len(times))) + f"hstack=inputs={len(times)}[s]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(parts) + ";" + stack,
                    "-map", "[s]", "-frames:v", "1", "-q:v", "4", str(out)], check=True)
    return out


def sheets_cmd(day, log=print):
    path = day_dir(day) / "scan.json"
    scan = read_json(path)
    tdir = day_dir(day) / "thumbs"
    tdir.mkdir(parents=True, exist_ok=True)
    for c in scan["candidates"]:
        out = tdir / f"{c['id']}.jpg"
        if not out.is_file():
            a, b = c["window"]
            p = c["spike_s"]
            thumbs(video_of(c["stem"]), [a + 1, p - 2, p, p + 1.5, min(b - 0.5, p + 5)], out)
        c["thumbs"] = f"thumbs/{out.name}"
    write_json(path, scan)
    log(f"  {len(scan['candidates'])} rows of thumbnails in {tdir}")
    return scan


OVERVIEW_EVERY = 10.0          # one frame every 10 s for the overview sheets (looked at by eye: stores, NPCs, fights)
OVERVIEW_COLS, OVERVIEW_ROWS = 6, 5   # 30 frames a sheet: 5 minutes of footage


def frame_at(video, t, out, w=320, h=180):
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(video), "-frames:v", "1",
                    "-vf", f"scale={w}:{h}", "-q:v", "4", str(out)], check=True)
    return out


def overview_cmd(stems, day, every=OVERVIEW_EVERY, log=print):
    """Sheets of one frame every `every` s (6 x 5, row by row: sheet k starts at k * 30 * every s), for looking by eye."""
    import subprocess
    import tempfile
    import meme_short
    odir = day_dir(day) / "overview"
    odir.mkdir(parents=True, exist_ok=True)
    per = OVERVIEW_COLS * OVERVIEW_ROWS
    for stem in stems:
        video = video_of(stem)
        duration = float(meme_short.ex.probe(video)["format"]["duration"])
        times = [k * every for k in range(int((duration - 1.0) // every) + 1)]
        with tempfile.TemporaryDirectory() as tmp:
            for i, t in enumerate(times):
                frame_at(video, t, Path(tmp) / f"f{i:04d}.jpg")
            for s in range(0, len(times), per):
                out = odir / f"{stem[-14:]}_{int(times[s]):05d}s.jpg"
                n = min(per, len(times) - s)
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-start_number", str(s), "-i", str(Path(tmp) / "f%04d.jpg"),
                                "-frames:v", "1", "-vf", f"tile={OVERVIEW_COLS}x{OVERVIEW_ROWS}:padding=2", "-q:v", "4",
                                str(out)] if n == per else
                               ["ffmpeg", "-v", "error", "-y", "-start_number", str(s), "-i",
                                str(Path(tmp) / "f%04d.jpg"), "-frames:v", "1", "-vf",
                                f"tile={OVERVIEW_COLS}x{math.ceil(n / OVERVIEW_COLS)}:nb_frames={n}:padding=2",
                                "-q:v", "4", str(out)], check=True)
        log(f"  {stem}: {len(times)} frames, {math.ceil(len(times) / per)} sheet(s)")


def strip_cmd(stem, a, b, every, out, cols=8):
    """One sheet of frames from a to b every `every` s (cols across), for timing a moment by eye."""
    import subprocess
    import tempfile
    video = video_of(stem)
    times = []
    t = a
    while t <= b + 1e-6:
        times.append(round(t, 2))
        t += every
    with tempfile.TemporaryDirectory() as tmp:
        for i, t in enumerate(times):
            frame_at(video, t, Path(tmp) / f"f{i:04d}.jpg")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(Path(tmp) / "f%04d.jpg"), "-frames:v", "1", "-vf",
                        f"tile={cols}x{math.ceil(len(times) / cols)}:nb_frames={len(times)}:padding=2", "-q:v", "4",
                        str(out)], check=True)
    return times


def preview(stem, a, b, out):
    """A small preview of a..b with its sound (854x480), for judging on the page with sound."""
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(video_of(stem)),
                    "-vf", "scale=854:480", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac",
                    "-b:a", "128k", "-movflags", "+faststart", str(out)], check=True)
    return out


# --- 6. page: the one review page, from judged.json (what was seen, written after looking at the frames) ---

TYPES = {"npc_fight": "funny: NPC fight", "npc_dialogue": "funny: NPC dialogue", "ragdoll": "funny: ragdoll",
         "crash_dialogue": "funny: crash during dialogue", "robbery": "robbery", "other": "other"}
CSS = """body{font:15px/1.45 system-ui,Segoe UI,Arial,sans-serif;margin:0 auto;max-width:1100px;padding:16px 18px 60px;
background:#111;color:#e8e8e8}h1{font-size:24px;margin:4px 0}h2{margin-top:34px;border-bottom:1px solid #333;padding-bottom:4px}
.steps{background:#1c2230;border-radius:8px;padding:10px 16px}.c{background:#1a1a1a;border:1px solid #2c2c2c;border-radius:10px;
padding:12px 14px;margin:14px 0}.c h3{margin:0 0 6px;font-size:18px}.tag{display:inline-block;border-radius:5px;padding:1px 8px;
margin-right:6px;font-size:13px;background:#333}.funny{background:#5b3a00}.robbery{background:#123f5c}.other{background:#3a3a3a}
.flag{background:#6b1f1f}img.t{width:100%;max-width:1080px;border-radius:6px;display:block;margin:6px 0}video{width:100%;
max-width:854px;border-radius:6px;background:#000}.k{color:#9aa}.say{font-style:italic;color:#ffe9a8}code{background:#222;
padding:1px 5px;border-radius:4px}table.r td{padding:2px 10px 2px 0;vertical-align:top}"""


def esc(s):
    import html
    return html.escape(str(s if s is not None else ""))


def page_cmd(day, log=print):
    """index.html from judged.json: thumbnails (5 frames a row) and a small preview with sound for every candidate and
    every robbery step, made once."""
    d = day_dir(day)
    judged = read_json(d / "judged.json")
    if not judged:
        raise SystemExit(f"ERROR: no {d / 'judged.json'}: write what was seen first")
    (d / "thumbs").mkdir(parents=True, exist_ok=True)
    (d / "previews").mkdir(parents=True, exist_ok=True)

    def media(key, stem, a, b, at):
        th, pv = d / "thumbs" / f"{key}.jpg", d / "previews" / f"{key}.mp4"
        if not th.is_file():
            thumbs(video_of(stem), at, th)
        if not pv.is_file():
            preview(stem, a, b, pv)
        return f"thumbs/{th.name}", f"previews/{pv.name}"

    out = [f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,"
           f"initial-scale=1'><title>Review {esc(day)}</title><style>{CSS}</style></head><body>",
           f"<h1>Today's footage, {esc(day)}: pick what is funny</h1>",
           "<div class='steps'><b>How to use this page (about 10 minutes)</b><ol>"
           "<li>Go down the list. For each one, look at the 5 pictures, then <b>press play</b> on the small video "
           "(it has the game's sound).</li>"
           "<li>If it made you laugh, note its number (e.g. <b>#3</b>). If not, skip it. The order is only my "
           "guess: you decide.</li>"
           "<li>The drafts I already made are marked <b>DRAFT MADE</b>; their files are listed in <code>picks.md"
           "</code> next to this page.</li>"
           "<li>The robberies are at the bottom: entry, grab, exit, and the wanted stars.</li>"
           "<li>Tell me the numbers you like (and the caption you want) and I'll finish those.</li></ol></div>"]
    for note in judged.get("notes", []):
        out.append(f"<p class='k'>{esc(note)}</p>")
    cands = sorted(judged.get("candidates", []), key=lambda c: c.get("rank", 99))[:20]
    out.append(f"<h2>Candidates, best guess first ({len(cands)})</h2>")
    for n, c in enumerate(cands, 1):
        at = c.get("thumbs_at") or [c["from_s"] + k * (c["to_s"] - c["from_s"]) / 4 for k in range(5)]
        th, pv = media(c["id"], c["stem"], c["from_s"], c["to_s"], at)
        kind = TYPES.get(c.get("type"), c.get("type", "other"))
        cls = "funny" if kind.startswith("funny") else "robbery" if kind == "robbery" else "other"
        out += [f"<div class='c' id='c{n}'><h3>#{n} &nbsp;{esc(c.get('title') or c.get('what', '')[:60])}</h3>",
                f"<span class='tag {cls}'>{esc(kind)}</span>"
                + (f"<span class='tag funny'>DRAFT MADE: {esc(c['draft'])}</span>" if c.get("draft") else "")
                + "".join(f"<span class='tag flag'>{esc(f)}</span>" for f in c.get("flags", [])),
                f"<p><b>{esc(c['stem'][-15:])}</b> &nbsp; {hms(c['from_s'])} to {hms(c['to_s'])} "
                f"<span class='k'>({c['from_s']:.1f}-{c['to_s']:.1f} s of the file)</span></p>",
                f"<img class='t' src='{th}' loading='lazy' alt='frames'>",
                f"<video controls preload='none' src='{pv}'></video>",
                f"<p><b>What happens:</b> {esc(c.get('what', ''))}</p>",
                "<p><b>Dialogue:</b> " + (" / ".join(f"<span class='say'>\"{esc(x)}\"</span>" for x in c.get("lines", []))
                                         or "<span class='k'>none heard</span>") + "</p>"]
        if c.get("why"):
            out.append(f"<p class='k'>Why it might work: {esc(c['why'])}. Weakest point: {esc(c.get('weak', '-'))}</p>")
        out.append("</div>")
    robs = judged.get("robberies", [])
    out.append(f"<h2>Robberies, start to finish ({len(robs)})</h2>")
    for n, r in enumerate(robs, 1):
        steps = [(s["name"], s) for s in r.get("steps", [])] or [(k, r[k]) for k in ("entry", "grab", "exit", "after")
                                                                    if r.get(k)]
        out += [f"<div class='c'><h3>Robbery {n}: {esc(r.get('title', ''))}</h3>",
                f"<p><b>{esc(r['stem'][-15:])}</b> &nbsp; {hms(r['from_s'])} to {hms(r['to_s'])}. "
                f"<b>Wanted stars:</b> {esc(r.get('stars', '?'))}</p>", f"<p>{esc(r.get('what', ''))}</p>",
                "<table class='r'>"]
        for k, s in steps:
            other = f" of {esc(s['stem'][-15:])}" if s.get("stem") and s["stem"] != r["stem"] else ""
            out.append(f"<tr><td><b>{esc(k)}</b></td><td>{hms(s['from_s'])}-{hms(s['to_s'])}{other}</td>"
                       f"<td>{esc(s.get('what', ''))}</td></tr>")
        out.append("</table>")
        for k, s in steps:
            at = s.get("thumbs_at") or [s["from_s"] + j * (s["to_s"] - s["from_s"]) / 4 for j in range(5)]
            th, pv = media(f"rob{n}_{re.sub(r'[^a-z0-9]+', '_', k.lower())}", s.get("stem", r["stem"]), s["from_s"],
                           s["to_s"], at)
            out += [f"<p class='k'>{k}</p><img class='t' src='{th}' loading='lazy' alt='{k}'>",
                    f"<video controls preload='none' src='{pv}'></video>"]
        out.append("</div>")
    out.append("</body></html>")
    (d / "index.html").write_text("\n".join(out), encoding="utf-8")
    log(f"  wrote {d / 'index.html'}")
    return d / "index.html"


def main(argv=None):
    import fastff
    fastff.use()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=("measure", "spikes", "hear", "transcribe", "look", "sheets", "overview", "page"))
    ap.add_argument("stems", nargs="*")
    ap.add_argument("--day", default=time.strftime("%Y-%m-%d"))
    a = ap.parse_args(argv)
    import heavy
    heavy.main_guard(f"day_scan.py {a.what}")
    t = time.perf_counter()
    if a.what == "measure":
        measure(a.stems)
    elif a.what == "spikes":
        spikes_cmd(a.stems, a.day)
    elif a.what == "hear":
        hear_cmd(a.day)
    elif a.what == "look":
        look_cmd(a.day)
    elif a.what == "overview":
        overview_cmd(a.stems, a.day)
    elif a.what == "transcribe":
        transcribe_cmd(a.stems, a.day)
    elif a.what == "page":
        page_cmd(a.day)
    else:
        sheets_cmd(a.day)
    print(f"day_scan.py {a.what}: {time.perf_counter() - t:.0f} s")


if __name__ == "__main__":
    sys.exit(main())
