"""A day's new recordings, scanned cheaply for meme and robbery candidates the owner judges (Nov 28).

Why: the automatic finder can't judge "funny" (it picked the GPS crash for the swearing in its dialogue). So this only
finds and ranks candidates and shows them on one page the owner can judge fast; the owner makes the pick.

  .\\.venv\\Scripts\\python.exe day_scan.py measure  STEM [STEM ...]   clip.py's own pass (loudness, picture, HUD stars)
  .\\.venv\\Scripts\\python.exe day_scan.py spikes   STEM [STEM ...]   candidate windows from those series -> scan.json
  .\\.venv-tts\\Scripts\\python.exe day_scan.py hear                  Whisper on each candidate window only
  .\\.venv\\Scripts\\python.exe day_scan.py look                      the vision model on each candidate's punch only
  .\\.venv\\Scripts\\python.exe day_scan.py sheets                    thumbnails for the review page

Cheap passes first: `measure` is the run /launch and the bank make of every recording (launch.run_clip into the bank's
clip_runs, so a later `bank.py build` reuses it and asks nothing again); `spikes` reads its timeline.csv. Whisper and the
vision model then run only around the spikes. Nothing here writes the bank, the index, _used_footage.json or
posted_topics.json; everything goes to the day's review folder (DAY_DIR).
"""
import argparse
import csv
import json
import math
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
        t = float(rows[i]["time_s"])
        if t < EDGE or t > duration - EDGE or phone[i] or sm[i] <= 0:
            continue
        if any(abs(t - k[0]) < gap for k in keep):
            continue
        keep.append((t, round(sm[i], 2), round(az[i], 2), round(vz[i], 2)))
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


def main(argv=None):
    import fastff
    fastff.use()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=("measure", "spikes", "hear", "look", "sheets"))
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
    else:
        sheets_cmd(a.day)
    print(f"day_scan.py {a.what}: {time.perf_counter() - t:.0f} s")


if __name__ == "__main__":
    sys.exit(main())
