"""Footage indexer (PR 21, session 1): watch a gameplay recording and log what happens in each part of it.

For one recording it writes, outside the repo:

  C:\\AI\\gta\\library\\<video name>\\index.json    the segments (start, end, activity, wanted stars, notable lines)
  C:\\AI\\gta\\library\\<video name>\\review.html   one row per segment with a thumbnail, for a spot-check
  C:\\AI\\gta\\library\\library.json                one entry per indexed recording (library.py searches it)

How it works, in three steps:

1. One ffmpeg pass over the recording (on the GPU when that works) gives two things: the HUD crops twice a second,
   and one JPEG "sheet" for every EVERY_S seconds of video: 4 frames, 1.5 s apart, in a 2x2 grid. (Ollama turns
   every picture into the same number of tokens, so 4 frames cost the same time as 1, and a short event such as
   a shootout is much less likely to fall between two looks.)
2. CODE reads the HUD first, with clip.py's own detectors and the HUD profile (--profile, default gta5): is the
   minimap there, is the phone's call screen up, are wanted stars showing. New here: HOW MANY stars (count_stars).
   Then a local vision model (Ollama) looks at each sheet and answers in strict JSON: activity, vehicle,
   police_visible, one notable line, confidence. The model is told nothing about the HUD.
3. CODE merges the two into segments (build_segments). The HUD rules are applied per half second and always win:
   - phone call screen up                      -> phone
   - no minimap for the profile's stretch time -> cutscene (or the model's menu_map / loading)
   - in a vehicle with wanted stars showing    -> chase (a single frame can't show a chase; the stars can)
   cutscene, menu_map, loading and phone segments get never_use = true. They are cut at the HUD's own edges, so
   a segment that is not never_use holds no HUD-excluded time.

Every model answer is cached on disk (keyed by video, sheet time, model, prompt and sheet layout), so a re-run
costs seconds and a stopped run carries on where it was. The answer keys in eval/ are never read here.

Standard library only; ffmpeg and ffprobe on PATH; Ollama running.

Usage:
  python index.py <video> [--profile gta5]          index one recording
  python index.py --all C:\\AI\\gta\\input            index every recording in a folder, one after the other
  python index.py <video> --review-only             rebuild review.html from the saved index.json
  options: --model NAME  --every S (seconds per sheet)  --width PX  --limit-s S (only the first S seconds)
           --library DIR
"""

import argparse
import base64
import ctypes
import html
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import clip
import vlm

LIBRARY = Path(r"C:\AI\gta\library")
OFF_LIMITS = (Path(r"C:\AI\gta\heldout"),)   # the owner's rule: held-out footage is never indexed
VIDEO_EXTS = {".webm", ".mp4", ".mkv", ".mov"}
INDEX_VERSION = 1

# --- The model. Chosen by the speed probe on test1 (see the README section "Footage library"). ---
MODEL = "qwen3-vl:4b-instruct"
EVERY_S = 6.0             # seconds of video per sheet, and so per model call
TILE = (2, 2)             # frames per sheet, across x down, in reading order; they are EVERY_S / 4 = 1.5 s apart
FRAME_W = 640             # width of one frame on the sheet (16:9 footage: 640x360, a 1280x720 sheet)
JPEG_Q = 4                # ffmpeg -q:v for the sheets
PROMPT_VERSION = "index_v1"   # prompts/<PROMPT_VERSION>.txt; {GAME} is replaced by the profile's game
GAME_PLACEHOLDER = "{GAME}"
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 4096, "num_predict": 160}
KEEP_ALIVE = "5m"
CALL_TIMEOUT = 300.0
# Ollama's memory grows with every picture it is sent (measured: free memory fell from 12 to 4.4 GB over 60 calls).
RELOAD_BELOW_GB = 5.0     # with less free memory than this, the model is unloaded; the next call loads it again
RELOAD_MIN_CALLS = 10     # ...but not more often than every this many calls
MIN_FREE_RAM_GB = 2.0     # stop (answers so far stay cached) if even that leaves less free memory than this

ACTIVITIES = ("driving", "on_foot", "shootout", "chase", "flying", "boat", "swimming", "cutscene", "menu_map",
              "phone", "loading", "other")
NEVER_USE = ("cutscene", "menu_map", "loading", "phone")
VEHICLE_ACTIVITIES = ("driving", "boat", "flying")   # with wanted stars showing, these become "chase"
NO_HUD_KEEPS = ("cutscene", "menu_map", "loading")   # the model's label is kept where the minimap is gone
MAX_NOTABLE_WORDS = 15
# The notable line comes first, so the model describes the frame before it picks an activity (thinking is off).
SCHEMA = {"type": "object",
          "properties": {"notable": {"type": "string"},
                         "activity": {"type": "string", "enum": list(ACTIVITIES)},
                         "vehicle": {"type": "string"},
                         "police_visible": {"type": "boolean"},
                         "confidence": {"type": "number"}},
          "required": ["notable", "activity", "vehicle", "police_visible", "confidence"]}

# --- Merging frames into segments ---
FLICKER = ("other",)          # a lone sheet with one of these labels, between two sheets that agree, takes theirs
MIN_SEGMENT_S = 2.0           # a shorter piece of gameplay joins its longer neighbour (a star flicker, say)
STAR_COUNT_MIN_S = 1.0        # a star count must show for this long in a segment to be its max_stars
STAR_GAP_S = 4.0              # stars gone for up to this long, with stars before and after, still count as showing
MAX_NOTABLE_LINES = 6         # notable lines kept per segment

# --- Counting wanted stars. An earned star has a dark outline around a lighter middle (white, or grey while it
# flashes); an empty slot is a flat grey shape with no outline. Defaults are for GTA V; a profile can override
# them with an optional "count" object inside "stars" (clip.py ignores it). Set on test1/test2 frames. ---
STAR_COUNT = {"fill_from": "right",   # which end of the row the first star appears at
              "ring": [0.2, 1.6],     # the outline: pixels this far (px) inside the star's edge
              "core": 2.6,            # the middle: pixels at least this far inside the edge
              # Measured on test1: a white star reads outline 104, middle 239; a flashing grey one 44 and 100 (both
              # 0.44); an empty slot reads the same in both (1.0); ammo digits in the row reached 0.65.
              "max_ratio": 0.55,      # outline brightness / middle brightness of an earned star, at most
              "min_contrast": 30.0}   # and the middle is at least this much brighter (0-255) than the outline


class IndexError_(Exception):
    """The recording can't be indexed (off limits, no frames, Ollama gone): index.py stops with the reason."""


def hms(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def free_ram_gb():
    """Free physical memory in GB (Windows); None where it can't be read."""
    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("avail", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong),
                    ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
    try:
        st = Status()
        st.length = ctypes.sizeof(Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return st.avail / 2 ** 30
    except (AttributeError, OSError):
        return None


def off_limits(video):
    """The off-limits folder a video is in, or None."""
    path = Path(video).resolve()
    for folder in OFF_LIMITS:
        if folder.resolve() in path.parents:
            return folder
    return None


# --- Wanted-star count ---

def star_polygons(prof):
    """The outline of each star slot, as clip.star_template() draws it: 10 points in the profile's frame pixels."""
    polys = []
    for tx in prof.star_tips_x:
        cx, cy = tx, prof.star_tip_y + prof.star_radius
        polys.append([(cx + r * math.cos(a), cy + r * math.sin(a))
                      for i in range(10)
                      for a, r in [(-math.pi / 2 + i * math.pi / 5,
                                    prof.star_radius if i % 2 == 0 else prof.star_radius * prof.star_inner)]])
    return polys


def _edge_dist(px, py, poly):
    best = float("inf")
    for i in range(len(poly)):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % len(poly)]
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(px - ax - t * dx, py - ay - t * dy))
    return best


def _inside(px, py, poly):
    inside = False
    for i in range(len(poly)):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % len(poly)]
        if (ay > py) != (by > py) and px < ax + (py - ay) * (bx - ax) / (by - ay):
            inside = not inside
    return inside


def star_count_rule(prof):
    """STAR_COUNT with the profile's optional stars.count values on top."""
    rule = dict(STAR_COUNT)
    extra = (prof.data.get("stars") or {}).get("count") or {}
    rule.update({k: v for k, v in extra.items() if k in STAR_COUNT})
    return rule


def star_masks(prof, rule=None):
    """Per star slot: (outline pixels, middle pixels), as indexes into the stars box (row * width + column).

    The arms of neighbouring stars overlap, so an outline pixel that is in or near another slot's star is left out.
    """
    rule = rule or star_count_rule(prof)
    x0, y0, w, h = prof.star_box
    polys = star_polygons(prof)
    lo, hi = rule["ring"]
    masks = []
    for k, poly in enumerate(polys):
        ring, core = [], []
        others = [p for m, p in enumerate(polys) if m != k]
        for y in range(h):
            for x in range(w):
                px, py = x0 + x + 0.5, y0 + y + 0.5
                if abs(px - prof.star_tips_x[k]) > prof.star_radius + 1 or not _inside(px, py, poly):
                    continue
                d = _edge_dist(px, py, poly)
                if d >= rule["core"]:
                    core.append(y * w + x)
                elif lo <= d <= hi and not any(_inside(px, py, o) or _edge_dist(px, py, o) <= hi for o in others):
                    ring.append(y * w + x)
        masks.append((ring, core))
    return masks


def star_slots(rgb, prof, masks):
    """[(outline brightness, middle brightness)] per slot from one HUD frame (clip.hud_filter's layout)."""
    hud_w = prof.hud_w

    def lum(i):
        w = prof.star_box[2]
        p = ((i // w) * hud_w + i % w) * 3
        return 0.2126 * rgb[p] + 0.7152 * rgb[p + 1] + 0.0722 * rgb[p + 2]

    out = []
    for ring, core in masks:
        if not ring or not core:
            out.append((255.0, 0.0))
            continue
        out.append((sum(lum(i) for i in ring) / len(ring), sum(lum(i) for i in core) / len(core)))
    return out


def count_stars(slots, rule):
    """How many stars are earned: slots with a dark outline round a lighter middle, counted from the end the row
    fills from, stopping at the first slot without one."""
    order = slots[::-1] if rule["fill_from"] == "right" else slots
    n = 0
    for ring, core in order:
        if core - ring >= rule["min_contrast"] and ring <= rule["max_ratio"] * core:
            n += 1
        else:
            break
    return n


# --- One pass over the video: HUD samples and frames ---

def frame_name(t):
    return f"f_{round(t * 1000):09d}.jpg"


def decode_pass(ffmpeg, video, info, range_fix, decoder, prof, frame_dir, every, width, limit_s=None, tile=TILE):
    """One decode: the HUD crops at clip.HUD_FPS through a pipe, and a JPEG sheet per `every` seconds into frame_dir.

    Returns (HUD samples, sheet times, error). A HUD sample is (time, star score, minimap seen, phone seen, star
    count). Sheet k holds tile[0] x tile[1] frames in reading order, spread evenly over k * every .. (k + 1) * every
    seconds (each is the first picture at or after its time), and is saved as frame_name(k * every).
    """
    gap = every / (tile[0] * tile[1])
    dl = clip.gpu_download(info) + "," if decoder == "cuda" else ""
    conv = "in_color_matrix=bt709:in_range=tv:" if range_fix else ""
    hud_step = 1.0 / clip.HUD_FPS
    # select picks the first frame at or after each multiple of the step, so sample k really is at k * step
    # (fps= would give the frame half a step later). GPU frames are only downloaded after select has dropped the rest.
    graph = (f"[0:v:0]split=2[vh][vj];"
             f"[vh]select='gte(t,selected_n*{hud_step:g})',{dl}{clip.hud_filter(info, range_fix, prof)}[hud];"
             f"[vj]select='gte(t,selected_n*{gap:g})',{dl}"
             f"scale={width}:-2:{conv}out_color_matrix=bt601:out_range=pc,tile={tile[0]}x{tile[1]}[jpg]")
    rule, masks = star_count_rule(prof), star_masks(prof)
    hud = []
    frame_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=frame_dir) as tmp:
        with open(Path(tmp) / "stderr.txt", "w+", encoding="utf-8", errors="replace") as err:
            cmd = ([ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error"]
                   + (clip.GPU_INPUT if decoder == "cuda" else [])
                   + (["-t", f"{limit_s:g}"] if limit_s else [])
                   + ["-i", str(Path(video).resolve()), "-filter_complex", graph,
                      "-map", "[hud]", "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1",
                      "-map", "[jpg]", "-fps_mode", "passthrough", "-q:v", str(JPEG_Q), "-start_number", "0",
                      "k%06d.jpg"])
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err, cwd=tmp)
            while True:
                raw = proc.stdout.read(prof.frame_bytes)
                if len(raw) < prof.frame_bytes:
                    break
                hud.append((len(hud) * hud_step, clip.star_score(raw, prof), clip.minimap_seen(raw, prof),
                            clip.phone_seen(raw, prof), count_stars(star_slots(raw, prof, masks), rule)))
            proc.stdout.close()
            if proc.wait() != 0:
                err.seek(0)
                return None, None, "\n".join(err.read().strip().splitlines()[-15:])
        times = []
        for k, path in enumerate(sorted(Path(tmp).glob("k*.jpg"))):
            t = round(k * every, 3)
            path.replace(frame_dir / frame_name(t))
            times.append(t)
    return hud, times, None


def hud_timeline(hud, duration, prof):
    """The HUD per half-second step: stars showing, star count, phone stretches, no-minimap stretches, and whether
    the HUD rules are on at all (clip.py's own test: the minimap must be seen in the profile's min_share of samples)."""
    n = int(math.ceil(duration / clip.BUCKET))
    _, stars, seen, phone, excluded, phone_cut = clip.hud_steps([s[:4] for s in hud], n, prof)
    share = sum(1 for s in hud if s[2]) / len(hud) if hud else 0.0
    on = share >= prof.min_share
    count = clip.bucketize([(s[0], s[4]) for s in hud], n, max, 0)
    stars = [on and s for s in stars]
    # A step with stars showing counts at least 1, even where no outline was measured.
    count = [max(1, c) if s else 0 for s, c in zip(stars, count)]
    # clip.py's star detector loses the stars for a second or two against a busy background (test1 1:27.5-1:29:
    # palm leaves behind them). A wanted level doesn't end and come back that fast, so short gaps are bridged.
    i = 0
    while i < n:
        j = i
        while j < n and not stars[j]:
            j += 1
        if 0 < i < j < n and j - i <= round(STAR_GAP_S / clip.BUCKET):
            for k in range(i, j):
                stars[k], count[k] = True, min(count[i - 1], count[j])
        i = max(j, i + 1)
    return {"on": on, "n": n, "minimap_share": round(share, 3),
            "stars": stars,
            "count": count,
            "phone": [on and p for p in phone_cut],
            "no_minimap": [on and e and not p for e, p in zip(excluded, phone_cut)]}


# --- The model ---

def load_prompt(game, version=PROMPT_VERSION):
    """(prompt text for this game, sha256 of the prompt file)."""
    text, sha = vlm.load_prompt(version)
    if text.count(GAME_PLACEHOLDER) != 1:
        raise ValueError(f"prompts/{version}.txt must contain {GAME_PLACEHOLDER} exactly once")
    return text.replace(GAME_PLACEHOLDER, game), sha


def parse_answer(content):
    """The model's reply as a clean label; ValueError if it doesn't fit SCHEMA."""
    try:
        a = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"reply is not JSON ({e})") from None
    if not isinstance(a, dict):
        raise ValueError("reply is not a JSON object")
    activity = str(a.get("activity", "")).strip().lower().replace(" ", "_")
    if activity not in ACTIVITIES:
        raise ValueError(f"unknown activity {a.get('activity')!r}")
    if not isinstance(a.get("police_visible"), bool):
        raise ValueError(f"police_visible is not true/false: {a.get('police_visible')!r}")
    conf = a.get("confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        raise ValueError(f"confidence is not a number: {conf!r}")
    notable = " ".join(str(a.get("notable", "")).split())
    if not notable:
        raise ValueError("empty notable line")
    words = notable.split(" ")
    if len(words) > MAX_NOTABLE_WORDS:
        notable = " ".join(words[:MAX_NOTABLE_WORDS]).rstrip(".,;:") + "..."
    vehicle = " ".join(str(a.get("vehicle", "")).lower().replace("_", " ").split()).strip(" .") or "none"
    if vehicle in ("no", "n/a", "null", "na", "no vehicle"):
        vehicle = "none"
    return {"notable": notable, "activity": activity, "vehicle": vehicle, "police_visible": a["police_visible"],
            "confidence": round(min(1.0, max(0.0, float(conf))), 2)}


def build_request(model, prompt, image_b64, capabilities):
    body = {"model": model, "stream": False, "keep_alive": KEEP_ALIVE, "format": SCHEMA,
            "messages": [{"role": "user", "content": prompt, "images": [image_b64]}], "options": dict(OPTIONS)}
    if "thinking" in capabilities:
        body["think"] = False
    return body


def ask_model(model, prompt, jpeg, capabilities, http=vlm.http_json):
    """One frame, one question. A record with the answer (or the error), the raw reply and Ollama's timings.
    One more try with another seed if the reply can't be used. Raises IndexError_ if Ollama stops answering."""
    body = build_request(model, prompt, base64.b64encode(Path(jpeg).read_bytes()).decode("ascii"), capabilities)
    t0 = time.perf_counter()
    rec = {"answer": None, "error": None}
    for attempt in range(2):
        body["options"]["seed"] = OPTIONS["seed"] + attempt
        try:
            r = http(vlm.OLLAMA_URL + "/api/chat", body, timeout=CALL_TIMEOUT)
        except (urllib.error.URLError, OSError) as e:
            raise IndexError_(f"Ollama stopped answering at {vlm.OLLAMA_URL} ({e})") from None
        msg = r.get("message", {})
        rec.update(raw=msg.get("content", ""), load_s=round(r.get("load_duration", 0) / 1e9, 2),
                   prompt_tokens=r.get("prompt_eval_count"), prompt_s=round(r.get("prompt_eval_duration", 0) / 1e9, 2),
                   output_tokens=r.get("eval_count"), output_s=round(r.get("eval_duration", 0) / 1e9, 2),
                   attempts=attempt + 1)
        try:
            rec["answer"], rec["error"] = parse_answer(rec["raw"]), None
            break
        except ValueError as e:
            rec["error"] = f"unusable answer: {e}"
    rec["wall_s"] = round(time.perf_counter() - t0, 2)
    return rec


def cache_fields(video, t, every, width, model, digest, prompt_sha, think, tile=TILE):
    """Everything that decides the model's answer for one sheet. The video is identified by name and size (never
    hashed: it is gigabytes)."""
    return {"video": video.name, "video_bytes": video.stat().st_size if video.exists() else None,
            "t_ms": round(t * 1000), "every_s": every, "tile": list(tile), "frame_width": width, "jpeg_q": JPEG_Q,
            "model": model, "model_digest": digest,
            "prompt_version": PROMPT_VERSION, "prompt_sha256": prompt_sha, "schema": SCHEMA, "options": OPTIONS,
            "think": think}


def label_frames(video, times, frame_dir, model, info, prompt, cache_dir, every, width, ask=ask_model, log=print,
                 ram=free_ram_gb, unload=vlm.unload):
    """The model's record for every sheet, from the cache or one call each. Usable answers are cached at once, so a
    stopped run loses nothing. When memory runs low the model is unloaded (the next call loads it again); if that
    doesn't help, the run stops with IndexError_. Returns (records, stats)."""
    text, sha = prompt
    think = False if "thinking" in info["capabilities"] else None
    records, calls, hits, errors, model_s, t0 = [], 0, 0, 0, 0.0, time.perf_counter()
    reloads, since_reload = 0, 0
    for n, t in enumerate(times, 1):
        fields = cache_fields(video, t, every, width, model, info["digest"], sha, think)
        rec = vlm.cache_load(cache_dir, fields)
        if rec is None:
            free = ram()
            if free is not None and free < RELOAD_BELOW_GB and since_reload >= RELOAD_MIN_CALLS:
                unload(model)
                reloads, since_reload, free = reloads + 1, 0, ram()
                log(f"  memory was low: unloaded the model at sheet {n}, {free:.1f} GB free now; it loads again")
            if free is not None and free < MIN_FREE_RAM_GB:
                raise IndexError_(f"only {free:.1f} GB of memory free (needs {MIN_FREE_RAM_GB:g}); stopped at sheet "
                                  f"{n} of {len(times)}. The answers so far are cached: run the same command again.")
            rec = ask(model, text, frame_dir / frame_name(t), info["capabilities"])
            calls += 1
            since_reload += 1
            model_s += rec["wall_s"]
            if rec["error"] is None:
                vlm.cache_save(cache_dir, fields, rec)
        else:
            hits += 1
        errors += rec["error"] is not None
        records.append(rec)
        if n % 25 == 0 or n == len(times):
            done = time.perf_counter() - t0
            left = done / n * (len(times) - n)
            log(f"  sheet {n}/{len(times)} ({hms(t)})  {calls} asked, {hits} cached, {errors} error(s)  "
                f"{done:.0f} s so far, about {left:.0f} s left")
    return records, {"model_calls": calls, "cache_hits": hits, "model_errors": errors, "model_s": round(model_s, 1),
                     "sec_per_call": round(model_s / calls, 2) if calls else None, "reloads": reloads}


# --- Merging into segments ---

def fill_missing(labels):
    """Frames with no usable answer take the label of the nearest frame that has one ('other' if none has)."""
    known = [i for i, v in enumerate(labels) if v is not None]
    return [v if v is not None else (labels[min(known, key=lambda k: abs(k - i))] if known else "other")
            for i, v in enumerate(labels)]


def smooth_labels(labels):
    """A lone sheet labelled "other" (FLICKER) between two sheets that agree takes their label. Every other label
    stands: a sheet is 4 looks over 6 s, so one sheet of on_foot between two of driving is a real stop."""
    out = list(labels)
    for j in range(1, len(labels) - 1):
        if labels[j] in FLICKER and labels[j - 1] == labels[j + 1]:
            out[j] = labels[j - 1]
    return out


def step_labels(frame_labels, every, hud):
    """(label, why) per half-second step: the label of the sheet that covers it, then the HUD rules on top."""
    out = []
    for i in range(hud["n"]):
        j = min(len(frame_labels) - 1, int((i + 0.5) * clip.BUCKET / every))
        label, why = frame_labels[j], "model"
        if hud["phone"][i]:
            label, why = "phone", "hud: phone call screen"
        elif hud["no_minimap"][i]:
            if label not in NO_HUD_KEEPS:
                label = "cutscene"
            why = "hud: no minimap"
        elif label in VEHICLE_ACTIVITIES and hud["stars"][i]:
            label, why = "chase", "hud: vehicle with wanted stars"
        out.append((label, why))
    return out


def merge_runs(steps):
    """[[label, first step, end step]] for each run of equal labels."""
    runs = []
    for i, (label, _) in enumerate(steps):
        if runs and runs[-1][0] == label:
            runs[-1][2] = i + 1
        else:
            runs.append([label, i, i + 1])
    return runs


def absorb_short(runs, min_steps):
    """A piece of gameplay shorter than min_steps takes the label of its longer gameplay neighbour. never_use
    pieces are left alone on both sides: they never grow and are never swallowed."""
    runs = [list(r) for r in runs]
    while True:
        short = [k for k, r in enumerate(runs) if r[0] not in NEVER_USE and r[2] - r[1] < min_steps
                 and any(0 <= m < len(runs) and runs[m][0] not in NEVER_USE for m in (k - 1, k + 1))]
        if not short:
            return runs
        k = min(short, key=lambda k: runs[k][2] - runs[k][1])
        near = [runs[m] for m in (k - 1, k + 1) if 0 <= m < len(runs) and runs[m][0] not in NEVER_USE]
        runs[k][0] = max(near, key=lambda r: r[2] - r[1])[0]
        merged = []
        for r in runs:
            if merged and merged[-1][0] == r[0] and merged[-1][2] == r[1]:
                merged[-1][2] = r[2]
            else:
                merged.append(r)
        runs = merged


def build_segments(times, records, every, duration, hud):
    """The segments of one recording, from the model's frame records and the HUD timeline (hud_timeline)."""
    raw = [r["answer"]["activity"] if r.get("answer") else None for r in records]
    labels = smooth_labels(fill_missing(raw))
    steps = step_labels(labels, every, hud)
    segments = []
    for label, i0, i1 in absorb_short(merge_runs(steps), round(MIN_SEGMENT_S / clip.BUCKET)):
        start, end = round(i0 * clip.BUCKET, 2), round(min(duration, i1 * clip.BUCKET), 2)
        inside = [k for k, t in enumerate(times) if start <= t + every / 2 < end]   # sheets by their middle
        if not inside:   # shorter than a sheet: describe it with the sheet that covers its middle
            inside = [min(len(times) - 1, int((start + end) / 2 / every))]
        answers = [(times[k], records[k]["answer"]) for k in inside if records[k].get("answer")]
        counts = hud["count"][i0:i1]
        need = round(STAR_COUNT_MIN_S / clip.BUCKET)
        max_stars = max((c for c in range(1, 6) if sum(1 for v in counts if v >= c) >= min(need, len(counts))),
                        default=0)
        notable, seen = [], set()
        for t, a in answers:
            if a["notable"].lower() not in seen:
                seen.add(a["notable"].lower())
                notable.append({"t": t, "text": a["notable"]})
        if len(notable) > MAX_NOTABLE_LINES:
            pick = [round(k * (len(notable) - 1) / (MAX_NOTABLE_LINES - 1)) for k in range(MAX_NOTABLE_LINES)]
            notable = [notable[k] for k in pick]
        vehicles = Counter(a["vehicle"] for _, a in answers)
        whys = Counter(w for _, w in steps[i0:i1])
        mid = (start + end) / 2
        segments.append({
            "start_s": start, "end_s": end, "duration_s": round(end - start, 2), "activity": label,
            "never_use": label in NEVER_USE,
            "max_stars": max_stars, "stars_s": round(sum(1 for v in hud["stars"][i0:i1] if v) * clip.BUCKET, 1),
            "vehicle": vehicles.most_common(1)[0][0] if vehicles else "none",
            "police_visible": any(a["police_visible"] for _, a in answers),
            "notable": notable,
            "confidence": round(sum(a["confidence"] for _, a in answers) / len(answers), 2) if answers else 0.0,
            "decided_by": whys.most_common(1)[0][0],
            "model_said": dict(Counter(a["activity"] for _, a in answers)),
            "thumb_t": times[min(inside, key=lambda k: abs(times[k] + every / 2 - mid))]})
    return segments


# --- Output ---

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def update_library(library, index):
    """Add or replace this recording's entry in library.json."""
    path = library / "library.json"
    lib = read_json(path) or {"version": INDEX_VERSION, "videos": {}}
    segs = index["segments"]
    usable = [s for s in segs if not s["never_use"]]
    lib["videos"][index["video"]["stem"]] = {
        "video": index["video"]["path"], "duration_s": index["video"]["duration_s"],
        "profile": index["profile"]["name"], "game": index["profile"]["game"],
        "index": str(Path(index["video"]["stem"]) / "index.json"), "indexed": index["indexed"],
        "model": index["model"]["name"], "every_s": index["settings"]["every_s"],
        "segments": len(segs), "usable_s": round(sum(s["duration_s"] for s in usable), 1),
        "never_use_s": round(sum(s["duration_s"] for s in segs if s["never_use"]), 1),
        "seconds_by_activity": {a: round(sum(s["duration_s"] for s in segs if s["activity"] == a), 1)
                                for a in ACTIVITIES if any(s["activity"] == a for s in segs)}}
    lib["updated"] = index["indexed"]
    write_json(path, lib)
    return path


def index_video(video, profile=clip.DEFAULT_PROFILE, model=MODEL, every=EVERY_S, width=FRAME_W, library=LIBRARY,
                limit_s=None, ask=ask_model, ollama=vlm, log=print, ram=free_ram_gb):
    """Index one recording; returns the index (also written to <library>/<stem>/index.json)."""
    video = Path(video)
    if not video.is_file():
        raise IndexError_(f"video not found: {video}")
    barred = off_limits(video)
    if barred:
        raise IndexError_(f"{video} is in {barred}: held-out footage is never indexed (the owner's rule)")
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise IndexError_("ffmpeg/ffprobe not found on PATH")
    try:
        prof = clip.load_profile(profile)
        prompt = load_prompt(prof.game)
        model_info = ollama.check_ollama(model)
    except (clip.ProfileError, vlm.VlmError, OSError, ValueError) as e:
        raise IndexError_(str(e)) from None

    t_start = time.perf_counter()
    out_dir = library / video.stem
    frame_dir = out_dir / "frames"
    info = clip.probe(ffprobe, video)
    duration = min(info["duration"], limit_s) if limit_s else info["duration"]
    range_fix = clip.needs_range_fix(info["video_codec"], info["color_range"])
    log(f"Input: {video.name}  {info['width']}x{info['height']}  {hms(duration)}  profile {prof.name} ({prof.game})  "
        f"model {model}, one {TILE[0]}x{TILE[1]} sheet per {every:g} s")

    # Step 1: one decode for the HUD and the frames. Skipped when a run with the same settings already did it.
    pass_fields = {"video": video.name, "video_bytes": video.stat().st_size, "profile_sha256": prof.sha256,
                   "star_count": star_count_rule(prof), "hud_fps": clip.HUD_FPS, "every_s": every,
                   "tile": list(TILE), "frame_width": width, "jpeg_q": JPEG_Q, "limit_s": limit_s,
                   "version": INDEX_VERSION}
    saved = read_json(out_dir / "hud.json") or {}
    t0 = time.perf_counter()
    if (saved.get("fields") == json.loads(json.dumps(pass_fields))
            and all((frame_dir / frame_name(t)).is_file() for t in saved["frame_times"])):
        hud, times, decoder = [tuple(s) for s in saved["samples"]], saved["frame_times"], "saved"
        log(f"HUD samples and {len(times)} sheets are already there from an earlier run.")
    else:
        decoder, why = clip.pick_decoder(ffmpeg, video, info, False)
        log(f"Reading the HUD and saving sheets, decoding on the {'GPU' if decoder == 'cuda' else 'CPU'} ({why})...")
        hud, times, err = decode_pass(ffmpeg, video, info, range_fix, decoder, prof, frame_dir, every, width, limit_s)
        if err and decoder == "cuda":
            log("WARNING: GPU decoding failed; trying again on the CPU.\n" + err)
            decoder = "cpu"
            hud, times, err = decode_pass(ffmpeg, video, info, range_fix, decoder, prof, frame_dir, every, width,
                                          limit_s)
        if err:
            raise IndexError_("reading the video failed:\n" + err)
        if not times:
            raise IndexError_("ffmpeg gave no frames")
        write_json(out_dir / "hud.json", {"fields": pass_fields, "frame_times": times, "samples": hud})
    t_decode = time.perf_counter() - t0
    tl = hud_timeline(hud, duration, prof)
    log(f"HUD rules {'on' if tl['on'] else 'OFF (no ' + prof.game + ' minimap found)'}: minimap in "
        f"{tl['minimap_share']:.0%} of samples, wanted stars for {sum(tl['stars']) * clip.BUCKET:.0f} s, phone "
        f"{sum(tl['phone']) * clip.BUCKET:.0f} s, no minimap {sum(tl['no_minimap']) * clip.BUCKET:.0f} s")

    # Step 2: the model labels each frame.
    free = free_ram_gb()
    log(f"Asking {model} about {len(times)} sheets" + (f" ({free:.1f} GB of memory free)..." if free else "..."))
    try:
        records, stats = label_frames(video, times, frame_dir, model, model_info, prompt, library / "_cache", every,
                                      width, ask=ask, log=log, ram=ram, unload=ollama.unload)
    finally:
        ollama.unload(model)   # its memory goes back as soon as the sheets are done, or the run stops

    # Step 3: segments.
    segments = build_segments(times, records, every, duration, tl)
    old = read_json(out_dir / "index.json") or {}
    timing = {"decode": round(t_decode, 1), "model": stats["model_s"],
              "total": round(time.perf_counter() - t_start, 1), "sec_per_call": stats["sec_per_call"],
              "decoder": decoder, "model_calls": stats["model_calls"], "cache_hits": stats["cache_hits"],
              "model_reloads": stats["reloads"]}
    if stats["model_calls"] < len(times) and old.get("timing_s", {}).get("model_calls", 0) > stats["model_calls"]:
        # A mostly cached re-run says nothing about speed: keep the timing of the run that asked the model.
        timing = dict(old["timing_s"], last_rerun_total=timing["total"])
    index = {
        "version": INDEX_VERSION, "indexed": datetime.now().isoformat(timespec="seconds"),
        "video": {"stem": video.stem, "name": video.name, "path": str(video.resolve()),
                  "bytes": video.stat().st_size, "duration_s": round(duration, 3),
                  "full_duration_s": round(info["duration"], 3), "width": info["width"], "height": info["height"]},
        "profile": {"name": prof.name, "game": prof.game, "sha256": prof.sha256},
        "model": {"name": model, "digest": model_info["digest"], "prompt_version": PROMPT_VERSION,
                  "prompt_sha256": prompt[1], "errors": stats["model_errors"]},
        "settings": {"every_s": every, "tile": list(TILE), "frame_width": width, "limit_s": limit_s,
                     "never_use": list(NEVER_USE),
                     "min_segment_s": MIN_SEGMENT_S, "star_count": star_count_rule(prof)},
        "hud": {"on": tl["on"], "minimap_share": tl["minimap_share"],
                "stars_s": sum(tl["stars"]) * clip.BUCKET, "phone_s": sum(tl["phone"]) * clip.BUCKET,
                "no_minimap_s": sum(tl["no_minimap"]) * clip.BUCKET},
        "timing_s": timing,
        "segments": segments,
        "sheets": [{"t": t, "file": f"frames/{frame_name(t)}", "error": r["error"], **(r["answer"] or {}),
                    "hud_stars": max(tl["count"][int(t / clip.BUCKET):int((t + every) / clip.BUCKET)], default=0)}
                   for t, r in zip(times, records)]}
    write_json(out_dir / "index.json", index)
    update_library(library, index)
    write_review(index, out_dir / "review.html")
    usable = sum(s["duration_s"] for s in segments if not s["never_use"])
    log(f"{len(segments)} segments, {hms(usable)} usable, {hms(duration - usable)} never_use. "
        f"{stats['model_calls']} model call(s), {stats['cache_hits']} cached, {stats['model_errors']} error(s); "
        f"decode {t_decode:.0f} s, model {stats['model_s']:.0f} s"
        + (f" ({stats['sec_per_call']} s per sheet)" if stats["sec_per_call"] else ""))
    log(f"Wrote {out_dir / 'index.json'}\n      {out_dir / 'review.html'}")
    return index


# --- The review page ---

def write_review(index, out):
    """review.html next to index.json: one row per segment, never_use rows in red. Thumbnails are the frames the
    model saw (relative links into frames/), and a click on a time plays the source from there."""
    v, rows = index["video"], []
    video_url = quote(Path(os.path.relpath(v["path"], out.parent)).as_posix())
    for n, s in enumerate(index["segments"], 1):
        stars = "&#9733;" * s["max_stars"] if s["max_stars"] else "&ndash;"
        lines = "<br>".join(f'<span class="t">{hms(x["t"])}</span> {html.escape(x["text"])}' for x in s["notable"])
        said = ", ".join(f"{k} {c}" for k, c in sorted(s["model_said"].items(), key=lambda kv: -kv[1]))
        rows.append(
            f'<tr class="{"never" if s["never_use"] else "ok"}"><td>{n}</td>'
            f'<td><a href="frames/{frame_name(s["thumb_t"])}"><img loading="lazy" '
            f'src="frames/{frame_name(s["thumb_t"])}" alt="4 frames from {hms(s["thumb_t"])}"></a></td>'
            f'<td><button class="seek" data-t="{s["start_s"]}">{hms(s["start_s"])}&ndash;{hms(s["end_s"])}</button>'
            f'<br><span class="meta">{s["duration_s"]:g} s</span></td>'
            f'<td><b>{html.escape(s["activity"])}</b>{"<br><span class=tag>NEVER USE</span>" if s["never_use"] else ""}'
            f'<br><span class="meta">{html.escape(s["decided_by"])}</span></td>'
            f'<td class="stars">{stars}<br><span class="meta">{s["stars_s"]:g} s</span></td>'
            f'<td>{html.escape(s["vehicle"])}{"<br>police seen" if s["police_visible"] else ""}</td>'
            f'<td>{lines}<br><span class="meta">model said: {html.escape(said) or "nothing usable"}; '
            f'confidence {s["confidence"]:.2f}</span></td></tr>')
    segs = index["segments"]
    never = sum(s["duration_s"] for s in segs if s["never_use"])
    t = index["timing_s"]
    meta = (f"{hms(v['duration_s'])} &middot; profile {html.escape(index['profile']['name'])} "
            f"({html.escape(index['profile']['game'])}) &middot; {html.escape(index['model']['name'])}, one sheet of 4 frames per "
            f"{index['settings']['every_s']:g} s &middot; {len(segs)} segments, {hms(never)} never_use &middot; "
            f"HUD rules {'on' if index['hud']['on'] else 'OFF'} &middot; indexed {html.escape(index['indexed'])} in "
            f"{t.get('total', 0):.0f} s")
    out.write_text(REVIEW_PAGE.replace("{NAME}", html.escape(v["name"])).replace("{META}", meta)
                   .replace("{ROWS}", "".join(rows)).replace("{VIDEO_URL}", video_url), encoding="utf-8")


REVIEW_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{NAME} footage index</title>
<style>
:root { --bg:#0f1115; --panel:#171a21; --line:#2a2f3a; --text:#e6e8ec; --muted:#9aa3b2; --bad:#ef4444; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:14px/1.45 system-ui, "Segoe UI", sans-serif; }
header, section { max-width:1500px; margin:0 auto; padding:16px; }
h1 { font-size:20px; margin:0 0 4px; }
.meta, .legend { color:var(--muted); font-size:12px; }
.legend { font-size:14px; }
table { width:100%; border-collapse:collapse; background:var(--panel); border:1px solid var(--line); }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-weight:600; font-size:12px; position:sticky; top:0; background:var(--panel); }
td img { width:360px; aspect-ratio:16/9; object-fit:cover; display:block; background:#000; border-radius:4px; }
tr.never { background:#3a1518; }
tr.never td:first-child { border-left:4px solid var(--bad); }
.tag { color:#fca5a5; font-weight:700; font-size:12px; }
.stars { color:#fbbf24; white-space:nowrap; font-size:16px; }
.t { color:var(--muted); font-variant-numeric:tabular-nums; }
button.seek { font:inherit; color:var(--text); background:none; border:1px solid #64748b; border-radius:4px;
  padding:1px 6px; cursor:pointer; white-space:nowrap; }
#player { position:fixed; inset:0; background:rgba(0,0,0,.85); display:none; align-items:center;
  justify-content:center; flex-direction:column; gap:8px; z-index:10; padding:16px; }
#player.open { display:flex; }
#player video { max-width:min(1280px, 100%); max-height:80vh; background:#000; }
#player p { color:var(--muted); margin:0; }
#player button { font:inherit; color:var(--text); background:var(--panel); border:1px solid var(--line);
  border-radius:4px; padding:4px 12px; cursor:pointer; }
</style></head><body>
<header>
<h1>{NAME}: footage index</h1>
<div class="meta">{META}</div>
<p class="legend">One row per segment. The picture is one sheet the model saw: 4 frames, 1.5 s apart, in reading
order (click it for full size). <b style="color:#fca5a5">Red rows are never_use</b> (cutscene, menu or map,
loading, phone): the Shorts builder will not take footage from them. Stars are the most wanted stars the code counted
in the segment, with the seconds they showed. Click a time to play the recording from there; Esc closes the player.
"Decided by" says whether the label came from the vision model or from a HUD rule.</p>
</header>
<section>
<table><thead><tr><th>#</th><th>Frames</th><th>Time</th><th>Activity</th><th>Stars</th><th>Vehicle</th>
<th>What the model saw</th></tr></thead><tbody>{ROWS}</tbody></table>
</section>
<div id="player" role="dialog" aria-label="Source video player">
<video id="vid" controls preload="none"></video>
<p id="vidmsg"></p>
<button id="close">Close (Esc)</button>
</div>
<script>
const player = document.getElementById("player"), vid = document.getElementById("vid"),
  msg = document.getElementById("vidmsg");
function play(src, t) {
  msg.textContent = "Source video (" + decodeURIComponent(src) + "). If nothing plays, the file has been moved.";
  player.classList.add("open");
  const go = () => { vid.currentTime = t; vid.play().catch(() => {}); };
  if (vid.getAttribute("src") === src && vid.readyState >= 1) { go(); return; }
  vid.addEventListener("loadedmetadata", go, { once: true });
  vid.src = src;
}
function closePlayer() { vid.pause(); player.classList.remove("open"); }
document.querySelectorAll("[data-t]").forEach(el => el.addEventListener("click",
  () => play("{VIDEO_URL}", parseFloat(el.dataset.t))));
document.getElementById("close").addEventListener("click", closePlayer);
player.addEventListener("click", e => { if (e.target === player) closePlayer(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closePlayer(); });
</script>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description="Index a gameplay recording: what happens in each part of it.")
    ap.add_argument("video", type=Path, nargs="?")
    ap.add_argument("--all", type=Path, metavar="FOLDER", help="index every recording in this folder instead")
    ap.add_argument("--profile", default=clip.DEFAULT_PROFILE,
                    help=f"HUD profile, profiles/NAME.json (default {clip.DEFAULT_PROFILE})")
    ap.add_argument("--model", default=MODEL, help=f"Ollama vision model (default {MODEL})")
    ap.add_argument("--every", type=float, default=EVERY_S,
                    help=f"seconds of video per sheet of 4 frames (default {EVERY_S:g})")
    ap.add_argument("--width", type=int, default=FRAME_W, help=f"width of one frame on a sheet (default {FRAME_W})")
    ap.add_argument("--limit-s", type=float, help="index only the first S seconds")
    ap.add_argument("--library", type=Path, default=LIBRARY, help=f"library folder (default {LIBRARY})")
    ap.add_argument("--review-only", action="store_true", help="rebuild review.html from the saved index.json")
    args = ap.parse_args()
    if (args.video is None) == (args.all is None):
        ap.error("give one video, or --all FOLDER")
    if args.every <= 0 or args.width < 64:
        ap.error("need --every > 0 and --width >= 64")
    videos = [args.video] if args.video else sorted(p for p in args.all.iterdir()
                                                    if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
    if not videos:
        sys.exit(f"ERROR: no recordings in {args.all}")
    for n, video in enumerate(videos, 1):
        if len(videos) > 1:
            print(f"\n=== {n}/{len(videos)}: {video.name} ===", flush=True)
        try:
            if args.review_only:
                index = read_json(args.library / video.stem / "index.json")
                if index is None:
                    raise IndexError_(f"no index.json for {video.name} in {args.library}: index it first")
                write_review(index, args.library / video.stem / "review.html")
                print(f"Wrote {args.library / video.stem / 'review.html'}")
            else:
                index_video(video, args.profile, args.model, args.every, args.width, args.library, args.limit_s,
                            log=lambda m: print(m, flush=True))
        except IndexError_ as e:
            sys.exit(f"ERROR: {e}")


if __name__ == "__main__":
    main()
