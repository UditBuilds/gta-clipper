"""Stage 2 (v6): a local vision model vetoes candidate clips.

clip.py ranks candidates exactly as before (2 x --count of them). For each candidate this module builds one
contact sheet (SHEET_FRAMES frames of the clip in a grid, each labelled with its source time), asks a local
Ollama vision model what the clip shows, and vetoes the clip when the answer is one of VETO. The model never
ranks or moves a clip: choose() walks the candidates in rank order, skips the vetoed ones, takes the first
`count` that are left, and fills any shortfall with the best-ranked vetoed ones ("vetoed, used as filler").

An answer the code can't use (call failed, bad JSON, unknown category) never vetoes: the candidate is kept and
marked as a model error. Usable answers are cached on disk (see cache_fields), so a re-run doesn't call the
model again. Python standard library + ffmpeg only.
"""

import base64
import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLLAMA_URL = "http://localhost:11434"
MODELS = ("qwen3-vl:4b-instruct", "qwen3-vl:8b-instruct")  # choices of clip.py --vlm (besides "off")
PROMPT_DIR = HERE / "prompts"
PROMPT_VERSION = "veto_v2"  # prompts/<PROMPT_VERSION>.txt; the last version tried on test1/test2 is frozen
DEFAULT_CACHE = Path(r"C:\AI\gta\vlm_cache")

CATEGORIES = ("stunt_or_explosion", "chase_or_shootout", "other_action",
              "clumsy_crash_or_stuck", "boring_driving_or_walking", "menu_or_cutscene")
VETO = ("clumsy_crash_or_stuck", "boring_driving_or_walking", "menu_or_cutscene")
# The reason comes first, so the model describes the frames before it picks a category (thinking is off).
SCHEMA = {"type": "object",
          "properties": {"reason": {"type": "string"},
                         "player_stalled": {"type": "boolean"},
                         "category": {"type": "string", "enum": list(CATEGORIES)}},
          "required": ["reason", "player_stalled", "category"]}
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 4096, "num_predict": 300}
KEEP_ALIVE = "5m"        # while the candidates are judged; the model is unloaded (keep_alive 0) right after
CALL_TIMEOUT = 300.0     # seconds for one answer, model load included
UNLOAD_WAIT = 30.0       # seconds to wait for /api/ps to show the model gone

# --- Contact sheet. SHEET_VERSION is part of the cache key: bump it whenever the sheet changes. ---
# 1 = 8 frames at the midpoints of 8 equal slices (the plan; never sent to a model).
# 2 = the same, but the frame nearest the clip's peak is moved exactly onto peak_time_s and labelled PEAK.
SHEET_VERSION = 2
SHEET_FRAMES = 8
SHEET_COLS = 4           # 4 across, 2 down, in reading order
FRAME_W, FRAME_H = 384, 216  # a fifth of 1920x1080; the sheet is 1536x480 (multiples of 32, the model's pixel block)
LABEL_H = 24             # black bar above each frame for its label, so the label covers no HUD
LABEL_SIZE = 18
FONT = "C\\:/Windows/Fonts/arial.ttf"  # escaped for an ffmpeg filter
JPEG_Q = 3               # ffmpeg -q:v (2 best .. 31 worst); about 190 KB per sheet


class VlmError(Exception):
    """Ollama is not reachable, or the model is missing: clip.py stops instead of running without the veto."""


def hms(seconds):
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:04.1f}"


def is_veto(result):
    """True when a candidate's model result vetoes it. Errors never veto."""
    return result.get("error") is None and result.get("category") in VETO


def choose(vetoed, count):
    """[(candidate index, filler)] in final rank order: survivors first, then fillers, each in rank order.

    vetoed is one bool per candidate, in rank order. The first `count` candidates not vetoed are kept; if fewer
    than `count` survive, the best-ranked vetoed candidates fill the rest and are marked as fillers.
    """
    keep = [i for i, v in enumerate(vetoed) if not v][:count]
    fill = [i for i, v in enumerate(vetoed) if v][:count - len(keep)]
    return [(i, False) for i in keep] + [(i, True) for i in fill]


def sheet_times(start, end, peak):
    """(times, peak index): SHEET_FRAMES frames at the midpoints of equal slices of start..end, with the one
    nearest the peak moved onto it. Moving the nearest frame keeps the times in order. A peak outside the clip
    (its 0.5 s step can start up to 0.5 s before the clip) is held inside it."""
    step = (end - start) / SHEET_FRAMES
    times = [start + (i + 0.5) * step for i in range(SHEET_FRAMES)]
    p = min(max(peak, start), end - min(0.05, step / 2))
    k = min(range(SHEET_FRAMES), key=lambda i: abs(times[i] - p))
    times[k] = p
    return times, k


def sheet_filter(times, k, range_fix):
    """filter_complex for SHEET_FRAMES inputs (one per time) -> one labelled grid [out]."""
    # Same range handling as make_review.py: PS5 VP9 pixels are limited range whatever the header says.
    conv = "in_color_matrix=bt709:in_range=tv:" if range_fix else ""
    parts = []
    for i, t in enumerate(times):
        label = f"{i + 1}   {hms(t)}" + ("   PEAK" if i == k else "")
        parts.append(f"[{i}:v]scale={FRAME_W}:{FRAME_H}:force_original_aspect_ratio=decrease:"
                     f"{conv}out_color_matrix=bt601:out_range=pc,"
                     f"pad={FRAME_W}:{FRAME_H + LABEL_H}:(ow-iw)/2:{LABEL_H}+({FRAME_H}-ih)/2:black,"
                     f"drawtext=fontfile='{FONT}':text='{label.replace(':', chr(92) + ':')}':x=6:y=3:"
                     f"fontsize={LABEL_SIZE}:fontcolor={'yellow' if i == k else 'white'}[f{i}]")
    rows = (len(times) + SHEET_COLS - 1) // SHEET_COLS
    for r in range(rows):
        cells = "".join(f"[f{i}]" for i in range(r * SHEET_COLS, min(len(times), (r + 1) * SHEET_COLS)))
        parts.append(f"{cells}hstack=inputs={cells.count('[')}[r{r}]")
    parts.append("".join(f"[r{r}]" for r in range(rows)) + f"vstack=inputs={rows}[out]")
    return ";".join(parts)


def make_sheet(ffmpeg, video, clip, out, range_fix):
    """Write the candidate's contact sheet (JPEG) to out; return its frame times and the peak frame's index."""
    times, k = sheet_times(clip["start_s"], clip["end_s"], clip["peak_time_s"])
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for t in times:
        cmd += ["-ss", f"{t:.3f}", "-i", str(video)]
    cmd += ["-filter_complex", sheet_filter(times, k, range_fix), "-map", "[out]", "-frames:v", "1",
            "-q:v", str(JPEG_Q), str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError("contact sheet failed: " + "\n".join(proc.stderr.strip().splitlines()[-5:]))
    return [round(t, 2) for t in times], k


def load_prompt(version=PROMPT_VERSION):
    """(text, sha256 of the file's bytes) of prompts/<version>.txt."""
    raw = (PROMPT_DIR / f"{version}.txt").read_bytes()
    return raw.decode("utf-8").strip(), hashlib.sha256(raw).hexdigest()


def http_json(url, body=None, timeout=30.0):
    """GET (body None) or POST JSON; the decoded JSON reply."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def check_ollama(model, base=OLLAMA_URL, http=http_json):
    """{digest, capabilities, loaded}: the model's digest and capabilities, and the models Ollama has loaded now.

    Raises VlmError if Ollama doesn't answer or the model isn't installed.
    """
    try:
        tags = http(base + "/api/tags")
        found = [m for m in tags.get("models", []) if m.get("name") == model or m.get("model") == model]
        if not found:
            vision = sorted(m["name"] for m in tags.get("models", []) if m["name"].startswith("qwen3-vl"))
            raise VlmError(f"model {model} is not installed in Ollama (installed qwen3-vl models: "
                           f"{', '.join(vision) or 'none'}); run: ollama pull {model}")
        show = http(base + "/api/show", {"model": model})
        loaded = [m["name"] for m in http(base + "/api/ps").get("models", [])]
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise VlmError(f"Ollama is not reachable at {base} ({e}); start Ollama or use --vlm off") from None
    return {"digest": found[0].get("digest", ""), "capabilities": show.get("capabilities", []), "loaded": loaded}


def build_request(model, prompt, image_b64, capabilities):
    """The /api/chat body: one user message with the sheet, the answer schema, temperature 0 and a fixed seed.
    Thinking is turned off when the model has a thinking mode (think is left out otherwise)."""
    body = {"model": model, "stream": False, "keep_alive": KEEP_ALIVE, "format": SCHEMA,
            "messages": [{"role": "user", "content": prompt, "images": [image_b64]}], "options": dict(OPTIONS)}
    if "thinking" in capabilities:
        body["think"] = False
    return body


def parse_answer(content):
    """{reason, player_stalled, category} from the model's reply text; ValueError if it doesn't fit SCHEMA."""
    try:
        a = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"reply is not JSON ({e})") from None
    if not isinstance(a, dict):
        raise ValueError("reply is not a JSON object")
    if a.get("category") not in CATEGORIES:
        raise ValueError(f"unknown category {a.get('category')!r}")
    if not isinstance(a.get("player_stalled"), bool):
        raise ValueError(f"player_stalled is not true/false: {a.get('player_stalled')!r}")
    if not isinstance(a.get("reason"), str) or not a["reason"].strip():
        raise ValueError("empty reason")
    return {"reason": a["reason"].strip(), "player_stalled": a["player_stalled"], "category": a["category"]}


def ask(body, base=OLLAMA_URL, http=http_json):
    """One model call. A record with the parsed answer (or error), the raw reply and Ollama's timings."""
    t0 = time.perf_counter()
    try:
        r = http(base + "/api/chat", body, timeout=CALL_TIMEOUT)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {"answer": None, "error": f"call failed: {e}", "wall_s": round(time.perf_counter() - t0, 2)}
    msg = r.get("message", {})
    rec = {"raw": msg.get("content", ""), "thinking": msg.get("thinking") or "",
           "wall_s": round(time.perf_counter() - t0, 2), "load_s": round(r.get("load_duration", 0) / 1e9, 2),
           "prompt_tokens": r.get("prompt_eval_count"), "prompt_s": round(r.get("prompt_eval_duration", 0) / 1e9, 2),
           "output_tokens": r.get("eval_count"), "output_s": round(r.get("eval_duration", 0) / 1e9, 2),
           "done_reason": r.get("done_reason")}
    try:
        rec["answer"], rec["error"] = parse_answer(rec["raw"]), None
    except ValueError as e:
        rec["answer"], rec["error"] = None, f"unusable answer: {e}"
    return rec


def model_memory(model, base=OLLAMA_URL, http=http_json):
    """{size_bytes, vram_bytes} of the loaded model from /api/ps (vram < size: part of it runs on the CPU)."""
    for m in http(base + "/api/ps").get("models", []):
        if m.get("name") == model:
            return {"size_bytes": m.get("size"), "vram_bytes": m.get("size_vram")}
    return None


def unload(model, base=OLLAMA_URL, http=http_json, wait=UNLOAD_WAIT, sleep=time.sleep):
    """Ask Ollama to unload the model now (keep_alive 0), then wait until /api/ps no longer lists it.
    (seconds waited, gone)."""
    t0 = time.perf_counter()
    try:
        http(base + "/api/generate", {"model": model, "keep_alive": 0})
        while time.perf_counter() - t0 < wait:
            if model not in [m.get("name") for m in http(base + "/api/ps").get("models", [])]:
                return round(time.perf_counter() - t0, 2), True
            sleep(0.25)
    except (urllib.error.URLError, OSError, ValueError):
        pass
    return round(time.perf_counter() - t0, 2), False


def cache_fields(video, clip, model, digest, prompt_version, prompt_sha, think):
    """Everything that decides the model's answer for one candidate. The video is identified by its name and size
    (never hashed: the files are gigabytes)."""
    return {"video": video.name, "video_bytes": video.stat().st_size if video.exists() else None,
            "start_s": clip["start_s"], "end_s": clip["end_s"], "peak_time_s": clip["peak_time_s"],
            "model": model, "model_digest": digest, "prompt_version": prompt_version, "prompt_sha256": prompt_sha,
            "sheet_version": SHEET_VERSION, "options": OPTIONS, "think": think}


def cache_path(cache_dir, fields):
    key = hashlib.sha256(json.dumps(fields, sort_keys=True).encode("utf-8")).hexdigest()
    return Path(cache_dir) / Path(fields["video"]).stem / f"{key}.json"


def cache_load(cache_dir, fields):
    """The cached record for these fields, or None (also None if the file's own fields differ)."""
    path = cache_path(cache_dir, fields)
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return entry["record"] if entry.get("fields") == json.loads(json.dumps(fields)) else None


def cache_save(cache_dir, fields, record):
    path = cache_path(cache_dir, fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"fields": fields, "record": record}, indent=2), encoding="utf-8")
    tmp.replace(path)


def judge(candidates, video, model, info, prompt, sheet_paths, cache_dir, sheet_fn, ask_fn, log=print):
    """Model results for the candidates (rank order), one dict each.

    sheet_paths[i] is where candidate i's sheet is written (always rebuilt: the run folder shows it). All sheets
    are built first (ffmpeg), then each candidate takes its cached answer or one model call (ask_fn(body)).
    Only usable answers are cached, so a failed call is tried again on the next run.
    """
    text, sha = prompt
    think = False if "thinking" in info["capabilities"] else None
    sheets = [sheet_fn(c, p) for c, p in zip(candidates, sheet_paths)]
    results = []
    for n, (c, path, (times, k)) in enumerate(zip(candidates, sheet_paths, sheets), 1):
        fields = cache_fields(video, c, model, info["digest"], PROMPT_VERSION, sha, think)
        rec, cached = cache_load(cache_dir, fields), True
        if rec is None:
            cached = False
            body = build_request(model, text, base64.b64encode(Path(path).read_bytes()).decode("ascii"),
                                 info["capabilities"])
            rec = ask_fn(body)
            if rec["error"] is None:
                cache_save(cache_dir, fields, rec)
        a = rec["answer"] or {}
        res = {"category": a.get("category"), "player_stalled": a.get("player_stalled"),
               "reason": a.get("reason"), "error": rec["error"], "cached": cached,
               "thinking_chars": len(rec.get("thinking", "")), "sheet_times_s": times, "sheet_peak_frame": k + 1}
        res["vetoed"] = is_veto(res)
        res["call"] = {k2: rec.get(k2) for k2 in ("wall_s", "load_s", "prompt_tokens", "prompt_s",
                                                   "output_tokens", "output_s", "done_reason")}
        results.append(res)
        verdict = (f"model error ({rec['error']})" if rec["error"] else
                   f"{res['category']}{' -> VETO' if res['vetoed'] else ''}: {res['reason']}")
        log(f"  candidate {n}: {hms(c['start_s'])}-{hms(c['end_s'])}  "
            f"{'cached' if cached else str(rec.get('wall_s')) + ' s'}  {verdict}")
    return results
