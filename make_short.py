"""Turn one script into a finished, checked explainer Short.

  .venv-tts\\Scripts\\python.exe make_short.py script.json            check, pick pictures, render, write the files
  .venv-tts\\Scripts\\python.exe make_short.py script.json --check    only check and show the picks; render nothing

The script is an explainer.py script with a few more settings:

  "title"        the upload title: 32-46 characters, 1-2 words in ALL CAPS, no hashtags
  "description"  the upload description, without hashtags
  "hashtags"     3 to 5 of them, such as ["#GTA6", "#GTAVI", "#Shorts"]
  "topic", "topic_score": {"score": 1-5, "why": "..."}   how much the topic matters to every player (under 3 warns)
  each line:  "facts": ["release-date"]   the facts.json IDs that line states, or [] for none
  each shot:  "query": "swamp airboat", "region": "Grassrivers"   instead of "file" (see picker.py)

What it does, in order, stopping at the first step that finds a problem:
  1. checks the title, description, hashtags and word count, and every line's facts against facts.json (facts.py);
  2. picks a picture for every shot that asks for one (picker.py);
  3. renders with explainer.py and runs its pacing check;
  4. writes to C:\\AI\\gta\\shorts\\<date>-<slug>\\:
       short.mp4    the Short
       script.json  the script as you gave it (script_resolved.json is what was rendered, every query now a file)
       upload.txt   the title and the description with its hashtags
       claims.txt   every line with its fact IDs, their status and their sources
       picks.jpg    each shot's words next to the picture chosen for it
       pacing.txt   the pacing check
The Short is READY when its length is 34-39 s and the pacing check has no warning. Otherwise the files are still
written, the last line says NOT READY and why, and the exit code is 1. Nothing is ever uploaded.

Needs .venv-tts (it renders with explainer.py); the checks themselves are standard library only.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from datetime import date
from pathlib import Path

import facts as facts_file
import picker
import tag_media

GTA_ROOT = Path(r"C:\AI\gta")
SHORTS_DIR = GTA_ROOT / "shorts"
TITLE_CHARS = (32, 46)
CAPS_WORDS = (1, 2)
# Words that are always written in capitals and so are not emphasis.
ACRONYMS = {"GTA", "VI", "PS", "PC", "GB", "TB", "AM", "PM", "TV", "UK", "US", "USA", "NPC", "AI", "DLC", "RP", "HUD",
            "FPS", "XP", "ESRB", "PEGI", "GMT", "HDR", "SSD", "ID", "OK"}
HASHTAGS = (3, 5)
WORDS_TARGET = (95, 105)                  # about what fills 34-39 s; outside it warns
WORDS_LIMIT = (85, 115)                   # outside this the Short can't land in 34-39 s, so nothing is rendered
OWN_KEYS = ("topic", "topic_score", "description", "hashtags")     # settings explainer.py doesn't know
MIN_TOPIC_SCORE = 3
FONT = "C\\:/Windows/Fonts/arial.ttf"     # escaped for an ffmpeg filter
ROW_W, ROW_H, THUMB_W, THUMB_H = 1500, 300, 330, 280


class ShortError(Exception):
    """The script can't be made into a Short as it is. The text lists every problem."""


# --- The checks that need no picture and no render ---

def title_problems(title):
    """What is wrong with an upload title, as a list. The rules come from a top GTA 6 Shorts channel."""
    if not isinstance(title, str) or not title.strip():
        return ["title is missing"]
    title = title.strip()
    problems = []
    if not TITLE_CHARS[0] <= len(title) <= TITLE_CHARS[1]:
        problems.append(f"title is {len(title)} characters; it must be {TITLE_CHARS[0]}-{TITLE_CHARS[1]}: {title!r}")
    if "#" in title:
        problems.append("title has a hashtag; hashtags go in \"hashtags\"")
    words = re.findall(r"[A-Za-z][A-Za-z'\u2019-]*", title)
    caps = [w for w in words if sum(c.isalpha() for c in w) >= 2 and w.upper() == w and w.strip("-'") not in ACRONYMS]
    if not CAPS_WORDS[0] <= len(caps) <= CAPS_WORDS[1]:
        problems.append(f"title has {len(caps)} word(s) in ALL CAPS ({', '.join(caps) or 'none'}); it must be "
                        f"{CAPS_WORDS[0]}-{CAPS_WORDS[1]} (names like GTA and PS5 don't count)")
    return problems


def upload_problems(script):
    """What is wrong with the description, the hashtags and the topic score."""
    problems = []
    description = script.get("description")
    if not isinstance(description, str) or not description.strip():
        problems.append("description is missing: one or two plain sentences for the upload")
    elif "#" in description:
        problems.append("description has a hashtag in it; put hashtags in \"hashtags\"")
    tags = script.get("hashtags")
    if (not isinstance(tags, list) or not HASHTAGS[0] <= len(tags) <= HASHTAGS[1]
            or not all(isinstance(t, str) and re.fullmatch(r"#[A-Za-z0-9_]+", t) for t in tags)
            or len({t.lower() for t in tags}) != len(tags)):
        problems.append(f"hashtags must be {HASHTAGS[0]}-{HASHTAGS[1]} different tags such as "
                        "[\"#GTA6\", \"#GTAVI\", \"#Shorts\"]")
    score = script.get("topic_score")
    if score is not None:
        good = (isinstance(score, dict) and isinstance(score.get("score"), int) and not isinstance(score["score"], bool)
                and 1 <= score["score"] <= 5 and isinstance(score.get("why"), str) and score["why"].strip())
        if not good:
            problems.append("topic_score is {\"score\": 1-5, \"why\": \"one line\"}")
    return problems


def spoken_words(script):
    """How many words the voice says, counted as explainer.py counts them."""
    lines = script.get("lines") if isinstance(script.get("lines"), list) else []
    return sum(1 for line in lines if isinstance(line, dict) and isinstance(line.get("say"), str)
               for token in line["say"].split() if any(c.isalnum() for c in token))


def check_script(script, facts, tags, recent=None, allow_weak=False):
    """Every check that needs no render. Returns a dict: problems, warnings, claims, picks, resolved (the script
    explainer.py gets: its own settings and nothing else, every query now a file)."""
    problems = title_problems(script.get("title")) + upload_problems(script)
    warnings = []
    words = spoken_words(script)
    if not WORDS_LIMIT[0] <= words <= WORDS_LIMIT[1]:
        problems.append(f"the script has {words} spoken words; {WORDS_TARGET[0]}-{WORDS_TARGET[1]} fills a "
                        f"34-39 s Short, and outside {WORDS_LIMIT[0]}-{WORDS_LIMIT[1]} it can't fit")
    elif not WORDS_TARGET[0] <= words <= WORDS_TARGET[1]:
        warnings.append(f"the script has {words} spoken words; about {WORDS_TARGET[0]}-{WORDS_TARGET[1]} fills 34-39 s")
    score = script.get("topic_score")
    if isinstance(score, dict) and isinstance(score.get("score"), int) and score["score"] < MIN_TOPIC_SCORE:
        warnings.append(f"topic score {score['score']} of 5: this topic doesn't matter to most players on day one "
                        f"({score.get('why', '')})")
    fact_problems, fact_warnings, claims = facts_file.check_script(script, facts)
    resolved, picks, pick_problems, pick_warnings = picker.resolve_script(script, tags, recent, allow_weak=allow_weak)
    for key in OWN_KEYS:
        resolved.pop(key, None)
    for line in resolved.get("lines") if isinstance(resolved.get("lines"), list) else []:
        if isinstance(line, dict):
            line.pop("facts", None)
    return {"problems": problems + fact_problems + pick_problems, "warnings": warnings + fact_warnings + pick_warnings,
            "claims": claims, "picks": picks, "resolved": resolved, "words": words}


def upload_text(script):
    """upload.txt: the title, then the description with its hashtags under it."""
    return (f"TITLE\n{script['title'].strip()}\n\nDESCRIPTION\n{script['description'].strip()}\n\n"
            f"{' '.join(script['hashtags'])}\n")


def slug_of(title):
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


# --- picks.jpg ---

def crop_box(size, focus, aspect):
    """The largest box of that width/height aspect inside a picture, centred on the focus as far as it fits:
    (x, y, width, height) in pixels. This is about what the viewer sees of the picture."""
    width, height = size
    w, h = (width, width / aspect) if width / height < aspect else (height * aspect, height)
    fx, fy = focus if isinstance(focus, (list, tuple)) else ((focus if isinstance(focus, (int, float)) else 0.5), 0.5)
    x = min(max(fx * width - w / 2, 0), width - w)
    y = min(max(fy * height - h / 2, 0), height - h)
    return tuple(int(round(v)) for v in (x, y, w, h))


def pick_view(pick, entry, size):
    """The part of the picture a shot shows, as a crop box: a lock-on's own box, a 9:16 cut for a full-screen shot,
    16:9 for a compare, a square for a card."""
    if pick.get("box") and pick.get("beat") == "lockon":
        x, y, w, h = pick["box"]
        pad = 0.15                                  # a little of what is around the box
        x0, y0 = max(0, x - w * pad), max(0, y - h * pad)
        return tuple(int(v) for v in (x0, y0, min(size[0] - x0, w * (1 + 2 * pad)), min(size[1] - y0, h * (1 + 2 * pad))))
    names = {"left": 1 / 6, "center": 0.5, "centre": 0.5, "right": 5 / 6, "top": 1 / 6, "middle": 0.5, "bottom": 5 / 6}
    focus = pick.get("focus") or (entry or {}).get("focus") or [0.5, 0.5]
    focus = [names.get(f, f) if isinstance(f, str) else f for f in (focus if isinstance(focus, list) else [focus, 0.5])]
    focus = [f if isinstance(f, (int, float)) else 0.5 for f in (focus + [0.5])[:2]]
    aspect = {"full": 9 / 16, "banner": 9 / 16, "behind": 9 / 16}.get(pick.get("side") or pick.get("beat"), None)
    if pick.get("side") in ("top", "bottom"):
        aspect = 16 / 9
    if aspect is None:
        aspect = 1.0 if pick.get("beat") == "card" or size[0] > size[1] else 9 / 16
    return crop_box(size, focus, aspect)


def pick_row_text(pick, entry):
    """The words beside a picture on picks.jpg."""
    where = f"Line {pick['line']}, shot {pick['shot']}" + (f" ({pick['side']})" if pick.get("side") else "")
    how = (f"PICKED for \"{pick['query']}\"" if pick["picked"] else "NAMED in the script")
    lines = [f"{where}   |   {pick.get('beat') or 'shot'}   |   {how}" + ("   |   WEAK MATCH" if pick.get("weak") else "")
             + ("   |   NOT SHOWN: the loop ending covers it" if pick.get("shown") is False else "")]
    lines += textwrap.wrap("SAYS: " + pick["say"], 104)[:3]
    shown = [re.sub(r"[~*|]+", " ", t).strip() for t in pick.get("text", []) if t and t.strip()]
    lines.append("ON SCREEN: " + (" / ".join(shown) if shown else "(no words on this picture)"))
    name = Path(pick.get("file") or pick.get("path") or "?").name
    if entry:
        lines.append(f"FILE: {name}   ({entry.get('region') or entry.get('folder', '').split('/')[-1]}, "
                     f"{entry.get('type')}, {entry.get('shape')})")
        lines += textwrap.wrap(f"TAGS: {entry.get('main_subject')}: {', '.join(entry.get('subjects', []))}", 104)[:2]
    else:
        lines.append(f"FILE: {name}   (not tagged)")
    return "\n".join(lines)


def build_picks_sheet(picks, tags, out, root=GTA_ROOT):
    """picks.jpg: one row per picture in the Short, the part of it the viewer sees on the left and the line's words on
    the right. A green edge is a picked picture, blue one the script named, red a weak match, grey one that the
    render left no time for."""
    rows = [p for p in picks if p.get("path") or p.get("file")]
    if not rows:
        return None
    with tempfile.TemporaryDirectory(prefix="picks_") as tmp:
        tmp = Path(tmp)
        for n, pick in enumerate(rows):
            entry = tags.get(pick.get("file")) if pick.get("file") else None
            source = Path(root) / (pick.get("path") or picker.MEDIA_PREFIX + pick["file"])
            size = (entry or {}).get("size")
            if not size and source.is_file():
                size = list(tag_media.probe_media(source)[:2])
            (tmp / f"row_{n}.txt").write_text(pick_row_text(pick, entry), encoding="utf-8", newline="\n")
            colour = ("0x999999" if pick.get("shown") is False else "0xD92B2B" if pick.get("weak") else
                      "0x1E9E55" if pick["picked"] else "0x2B6CD9")
            base = (f"drawbox=x=0:y=0:w=10:h={ROW_H}:color={colour}:t=fill,"
                    f"drawbox=x=0:y={ROW_H - 2}:w={ROW_W}:h=2:color=0xCCCCCC:t=fill,"
                    f"drawtext=fontfile='{FONT}':textfile=row_{n}.txt:expansion=none:x={THUMB_W + 40}:y=18:"
                    f"fontsize=22:line_spacing=9:fontcolor=0x111111")
            canvas = ["-f", "lavfi", "-i", f"color=white:s={ROW_W}x{ROW_H}"]
            if size and source.is_file():
                x, y, w, h = pick_view(pick, entry, size)
                at = ["-ss", str((entry or {}).get("start_s") or 0)] if source.suffix.lower() in tag_media.VIDEO_EXTS else []
                graph = (f"[1:v]crop={w}:{h}:{x}:{y},scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=decrease[p];"
                         f"[0:v][p]overlay=20+({THUMB_W}-w)/2:({ROW_H}-h)/2,{base}")
                args = [*canvas, *at, "-i", str(source), "-filter_complex", graph]
            else:
                args = [*canvas, "-vf", base]
            done = subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-frames:v", "1", f"row_{n:03d}.png"],
                                  cwd=tmp, capture_output=True, text=True)
            if done.returncode:
                raise ShortError(f"picks.jpg: ffmpeg failed on {source.name}: {done.stderr.strip()[:300]}")
        columns = 1 if len(rows) <= 12 else 2
        per_column = -(-len(rows) // columns)
        done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", "1", "-i", "row_%03d.png", "-vf",
                               f"tile={columns}x{per_column}:color=white", "-frames:v", "1", "-q:v", "3", str(out)],
                              cwd=tmp, capture_output=True, text=True)
        if done.returncode:
            raise ShortError(f"picks.jpg: ffmpeg failed to join the rows: {done.stderr.strip()[:300]}")
    return out


# --- The whole run ---

def read_script(path):
    try:
        script = json.loads(Path(path).read_bytes().decode("utf-8-sig"))
    except OSError as e:
        raise ShortError(f"can't read the script: {e}") from None
    except ValueError as e:
        raise ShortError(f"{path} is not valid JSON: {e}") from None
    if not isinstance(script, dict):
        raise ShortError(f"{path} must hold one JSON object")
    return script


def explainer_problems(resolved, root, log=print):
    """What explainer.py itself says is wrong with the script it would get (a bad beat, a label too wide, a tag that
    isn't in the line), or None. It reads the script the way a render does, without rendering."""
    try:
        import explainer
    except ImportError as e:                        # the checks run anywhere; explainer.py needs .venv-tts
        log(f"  Note: explainer.py's own check was skipped ({e}); run with .venv-tts to include it")
        return None
    with tempfile.TemporaryDirectory(prefix="check_") as tmp:
        path = Path(tmp) / "script.json"
        path.write_text(json.dumps(resolved, ensure_ascii=False), encoding="utf-8")
        try:
            explainer.load_script(path, root)
        except explainer.ScriptError as e:
            return str(e)
    return None


def make_short(script_path, check_only=False, allow_weak=False, shorts_dir=SHORTS_DIR, root=GTA_ROOT, today=None,
               facts=None, tags=None, render=None, log=print):
    """Check a script and make its Short. Returns (folder or None, ready). ShortError lists what stops it.
    facts, tags and render are swapped for stand-ins by the tests."""
    script = read_script(script_path)
    try:
        facts = facts if facts is not None else facts_file.load_facts()
        tags = tags if tags is not None else tag_media.load_tags()
        used_log = Path(shorts_dir) / picker.USED_LOG.name
        name = f"{today or date.today().isoformat()}-{slug_of(str(script.get('title', '')))}"
        result = check_script(script, facts, tags, picker.recent_files(picker.load_used(used_log), name), allow_weak)
    except (facts_file.FactsError, tag_media.TagError, picker.PickError) as e:
        raise ShortError(str(e)) from None
    if not tags:
        result["warnings"].append("no pictures are tagged yet: run tag_media.py so shots can ask for pictures")
    for pick in result["picks"]:
        how = f"picked for \"{pick['query']}\"" if pick["picked"] else "named in the script"
        log(f"  line {pick['line']}, shot {pick['shot']}{' ' + pick['side'] if pick.get('side') else ''}: "
            f"{Path(pick.get('file') or pick.get('path') or '?').name}  ({how})")
    for warning in result["warnings"]:
        log(f"  WARNING: {warning}")
    if result["problems"]:
        raise ShortError("the script needs fixing before it can be made:\n  " + "\n  ".join(result["problems"]))
    if render is None:
        wrong = explainer_problems(result["resolved"], root, log)
        if wrong:
            raise ShortError(f"explainer.py can't render it: {wrong}")
    log(f"  Checks passed: {result['words']} spoken words, {len({f['id'] for c in result['claims'] for f in c['facts']})} "
        f"facts used, {sum(1 for p in result['picks'] if p['picked'])} pictures picked")
    if check_only:
        return None, True

    out = Path(shorts_dir) / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    resolved_path = out / "script_resolved.json"
    resolved_path.write_text(json.dumps(result["resolved"], indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "upload.txt").write_text(upload_text(script), encoding="utf-8")
    (out / "claims.txt").write_text(facts_file.claims_text(script, result["claims"], facts), encoding="utf-8")
    build_picks_sheet(result["picks"], tags, out / "picks.jpg", root)

    if render is None:
        import explainer                              # needs .venv-tts; the checks above don't
        try:
            explainer.render(resolved_path, root, shorts_dir, run_dir=out)
        except explainer.ScriptError as e:
            raise ShortError(f"explainer.py can't render it: {e}") from None
        pacing_lines = explainer.pacing_lines
    else:
        pacing_lines = render(resolved_path, out)

    run_log = json.loads((out / "log.json").read_text(encoding="utf-8"))
    pacing = run_log["pacing"]
    notes = [w for w in run_log.get("warnings", [])]
    (out / "pacing.txt").write_text("\n".join([*pacing_lines(pacing), *(f"  WARNING: {w}" for w in notes)]) + "\n",
                                    encoding="utf-8")
    not_ready = []
    if not pacing["length_ok"]:
        not_ready.append(f"it is {pacing['length_s']:.1f} s long; the target is {pacing['target_s'][0]}-"
                         f"{pacing['target_s'][1]} s (cut or add words, or change the speed, 1.1 at most)")
    not_ready += pacing["warnings"]
    ready = not not_ready
    # A picture of the last line can be left with no time of its own, because the loop ending shows the first shot
    # again. The sheet says so, and such a picture doesn't count as used.
    if isinstance(run_log.get("visuals"), list):
        on_screen = " ".join(str(v.get("source", "")) + " " + str(v.get("behind") or "") for v in run_log["visuals"])
        for pick in result["picks"]:
            pick["shown"] = Path(pick.get("path") or pick.get("file") or "?").name in on_screen
        build_picks_sheet(result["picks"], tags, out / "picks.jpg", root)
    if ready:
        picker.save_used(name, [p["file"] for p in result["picks"] if p.get("file") and p.get("shown") is not False],
                         used_log)
    log(f"Folder: {out}")
    for item in ("short.mp4", "script.json", "upload.txt", "claims.txt", "picks.jpg", "pacing.txt"):
        log(f"  {out / item}")
    log("READY: look at picks.jpg and claims.txt, then upload short.mp4 with upload.txt yourself." if ready else
        "NOT READY: " + "; ".join(not_ready))
    return out, ready


def main():
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Check a script and turn it into a finished Short.")
    parser.add_argument("script", type=Path)
    parser.add_argument("--check", action="store_true", help="only check the script and show its picks; render nothing")
    parser.add_argument("--allow-weak", action="store_true",
                        help="when nothing matches a query well, use the closest picture (it is marked on picks.jpg)")
    args = parser.parse_args()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ERROR: ffmpeg and ffprobe must be on PATH", file=sys.stderr)
        sys.exit(2)
    try:
        _, ready = make_short(args.script, args.check, args.allow_weak)
    except ShortError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0 if ready else 1)


if __name__ == "__main__":
    main()
