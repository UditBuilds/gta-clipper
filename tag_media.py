"""Tag Rockstar's GTA 6 pictures by what they show, so a Short can ask for "swamp airboat" instead of a file path.

A local vision model (qwen3-vl in Ollama) looks at every screenshot, and at the first and the middle frame of every
video loop, and answers with: a one-line description, the main subject, the things in view, the setting, the time of
day, who is in it, and where the main subject sits (the focus point). The tags are saved next to the media, outside
the repo:

  C:\\AI\\gta\\rockstar\\_tags.json            what the model said, one entry per file
  C:\\AI\\gta\\rockstar\\_tags_override.json   your fixes; whatever is in here wins over the model
  C:\\AI\\gta\\rockstar\\_tag_check\\index.html the check sheet: every thumbnail with its tags, and a way to fix them

A file that is already tagged is skipped unless --force is given. The override file is never written by this tool
and is applied every time the tags are read (load_tags), so a fix survives a --force re-tag.

A character's name can't be read from a face by the model. It comes from the folder ("People/Jason Duval"), and is
kept only when the model sees a person of that kind in the picture (a man, for Jason).

Standard library only; ffmpeg and ffprobe on PATH; Ollama running.

Usage:
  python tag_media.py                 tag what is new, then rebuild the check sheet
  python tag_media.py --force         tag everything again
  python tag_media.py --only Kalaga   only files whose path holds this text (may be repeated)
  python tag_media.py --sheet         only rebuild the check sheet
  python tag_media.py --model qwen3-vl:4b --out C:\\some\\other.json --only ...   (for comparing models)
"""

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
from datetime import date
from pathlib import Path

import vlm

HERE = Path(__file__).resolve().parent
MEDIA_ROOT = Path(r"C:\AI\gta\rockstar")
FOLDERS = ("GTAVI_Screenshots", "GTAVI_Videos", "GTAVI_Artwork_Wallpapers", "GTAVI_Ultimate_Edition_Benefits",
           "GTAVI_Vintage_Vice_City_Pack")           # what gets tagged, inside MEDIA_ROOT
# A file's type comes from its top folder. Cover art (the box art and the album's) is "key art" wherever it is.
TYPES = ("screenshot", "wallpaper", "edition", "key art", "video loop")
TOP_FOLDER_TYPES = {"GTAVI_Screenshots": "screenshot", "GTAVI_Videos": "video loop",
                    "GTAVI_Artwork_Wallpapers": "wallpaper", "GTAVI_Ultimate_Edition_Benefits": "edition",
                    "GTAVI_Vintage_Vice_City_Pack": "edition"}
SHAPES = ("phone", "portrait", "square", "landscape", "ultrawide")
OVERLAYS = ("big text", "logo", "rating card")       # what can be printed on top of a picture
TAGS_NAME, OVERRIDE_NAME, CHECK_DIR_NAME = "_tags.json", "_tags_override.json", "_tag_check"
PAGE_TEMPLATE = HERE / "tag_check_page.html"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}

# The "-instruct" builds answer straight away. The plain qwen3-vl:4b and :8b are the same models with a thinking mode
# that Ollama can't switch off for them: they spend the whole answer thinking and return nothing.
# 8b over 4b, on 20 files judged by eye (PR 19): the tags were as good from both (98 of 100 checks each), but 8b's focus
# point was within a tenth of the frame of the subject on 14 files against 8, and 4b answered the exact centre 8 times.
# 8b takes about 7 s a frame against 4.5 s, and a little of it runs on the CPU with 8 GB of video memory.
MODEL = "qwen3-vl:8b-instruct"
PROMPT_VERSION = 1        # saved with every tag; raise it when PROMPT or SCHEMA changes
TEXT_VERSION = 1          # the same for TEXT_PROMPT, the second question (is anything printed on top of the picture?)
SCAN_MIN_S, SCAN_STEP_S, SCAN_LIMIT_S = 4.0, 0.5, 20.0   # a video longer than this is searched for where its picture starts
BLACK_FILTER = "blackframe=amount=97:threshold=40"       # a frame this dark is a gap between cards, not a picture
SEND_LONG_SIDE = 1280     # the picture is shrunk to this before the model sees it (a 3840x2160 shot becomes 1280x720)
THUMB_WIDTH = 480         # the check sheet's thumbnails
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 4096, "num_predict": 400}
KEEP_ALIVE = "5m"
CALL_TIMEOUT = 300.0

SETTINGS = ("city", "suburb", "small town", "swamp", "forest", "mountain", "beach", "ocean", "river or lake",
            "highway", "street", "bridge", "port", "industrial", "farmland", "interior", "sky")
TIMES = ("day", "sunset", "night")
PEOPLE = ("man", "woman", "crowd")
MAX_SUBJECTS = 8

PROMPT = """You are tagging one official screenshot from the video game GTA 6, so that a video editor can find pictures by what they show.
Look only at the picture. Do not guess from what you know about the game, and do not name characters.

Answer in JSON with these fields:
- description: one plain sentence of 12 to 25 words that says what the picture mainly shows.
- main_subject: the one thing the picture is mostly about, in 1 to 3 words. Examples: "airboat", "police car", "woman with a gun", "city skyline".
- subjects: 3 to 8 things that are clearly visible, the most important first. Use short lowercase nouns. Examples: helicopter, airboat, alligator, car, truck, motorbike, boat, plane, police, gun, man, woman, crowd, skyline, beach, palm trees, bridge, neon sign, bar, store, house, road, swamp, mountain, river. List only what you can see.
- setting: where it is. One or more of: city, suburb, small town, swamp, forest, mountain, beach, ocean, river or lake, highway, street, bridge, port, industrial, farmland, interior, sky.
- time: day, sunset or night.
- people: who is clearly visible. Any of: man, woman, crowd. An empty list if nobody is.
- focus_x and focus_y: where the centre of the main subject is, each on a scale from 0 to 1000. focus_x goes across the picture's width (0 is the left edge, 1000 the right edge). focus_y goes down its height (0 is the top, 1000 the bottom)."""

# The description comes first, so the model looks before it picks tags (thinking is off).
SCHEMA = {"type": "object",
          "properties": {"description": {"type": "string"},
                         "main_subject": {"type": "string"},
                         "subjects": {"type": "array", "items": {"type": "string"}},
                         "setting": {"type": "array", "items": {"type": "string", "enum": list(SETTINGS)}},
                         "time": {"type": "string", "enum": list(TIMES)},
                         "people": {"type": "array", "items": {"type": "string", "enum": list(PEOPLE)}},
                         "focus_x": {"type": "integer"},
                         "focus_y": {"type": "integer"}},
          "required": ["description", "main_subject", "subjects", "setting", "time", "people", "focus_x", "focus_y"]}

TEXT_PROMPT = """Look at this picture from the video game GTA 6. I need to know if words or logos were added on top of it.

Answer in JSON with these fields:
- words_seen: the large words or logos you can read, 5 at most. An empty list if there are none.
- big_text: true if large words are printed over the picture, such as a title, a place name, a date, a slogan, a price or a caption. Signs, shop names, number plates and clothing that are part of the scene do not count.
- logo: true if there is a game or company logo on it, such as "Grand Theft Auto", a large "VI", the Rockstar Games "R" badge, PlayStation or Xbox.
- rating_card: true if there is an age-rating label, such as ESRB, PEGI, "Rating Pending", "Mature 17+" or "May contain content inappropriate for children"."""

TEXT_SCHEMA = {"type": "object",
               "properties": {"words_seen": {"type": "array", "items": {"type": "string"}},
                              "big_text": {"type": "boolean"}, "logo": {"type": "boolean"},
                              "rating_card": {"type": "boolean"}},
               "required": ["words_seen", "big_text", "logo", "rating_card"]}

# Who each character folder (and video file name) is about, and which kind of person the model must see to keep the name.
CHARACTERS = {"jason duval": [("Jason", "man")],
              "lucia caminos": [("Lucia", "woman")],
              "jason and lucia": [("Jason", "man"), ("Lucia", "woman")],
              "jason lucia": [("Jason", "man"), ("Lucia", "woman")],
              "boobie ike": [("Boobie Ike", "man")],
              "brian heder": [("Brian Heder", "man")],
              "cal hampton": [("Cal Hampton", "man")],
              "drequan priest": [("DreQuan Priest", "man")],
              "raul bautista": [("Raul Bautista", "man")],
              "real dimez": [("Real Dimez", "woman")]}
FIELDS = ("description", "main_subject", "subjects", "setting", "time", "people", "names", "focus", "region",
          "type", "overlay", "start_s")


class TagError(Exception):
    """Something the owner has to fix before tagging can go on."""


# --- Files ---

def media_files(root=MEDIA_ROOT, folders=FOLDERS):
    """Every picture and video to tag, as paths relative to root with forward slashes, in a steady order."""
    found = []
    for folder in folders:
        base = Path(root) / folder
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS:
                found.append(path.relative_to(root).as_posix())
    return sorted(found, key=str.lower)


def folder_facts(rel):
    """What the path alone says: {"region": "Grassrivers" or None, "character": "jason duval" or None}."""
    parts = Path(rel).parts
    region = None
    for marker in ("Places", "Postcards"):          # .../Places/<region>/<file>, .../Postcards/<region>/<file>
        if marker in parts[:-2]:
            region = parts[parts.index(marker) + 1].replace("_", " ")
    # The character is in the folder's name ("People/Jason Duval", "Boobie_Ike") or the file's ("Jason_Duval_Video_Clip").
    plain = [name.lower().replace("_", " ") for name in (*parts[:-1][::-1], Path(rel).stem)]
    known = sorted(CHARACTERS, key=len, reverse=True)
    character = next((who for name in plain for who in known if name.startswith(who)), None)
    return {"region": region, "character": character}


def media_type(rel):
    """screenshot, wallpaper, edition, key art or video loop, from where the file is."""
    kind = TOP_FOLDER_TYPES.get(Path(rel).parts[0], "screenshot")
    return "key art" if "cover_art" in rel.lower() else kind


def shape_of(width, height):
    """phone (taller than 9:16), portrait, square, landscape or ultrawide."""
    ratio = width / height
    if ratio >= 2.2:
        return "ultrawide"
    if ratio > 1.15:
        return "landscape"
    if ratio >= 0.87:
        return "square"
    return "portrait" if ratio > 0.5 else "phone"


def group_of(rel):
    """Files that are the same picture cut to different shapes share a group: a wallpaper's folder, with or without
    logos. Every other file is its own group."""
    if Path(rel).parts[0] != "GTAVI_Artwork_Wallpapers":
        return rel
    folder = Path(rel).parent.as_posix()
    for ending in ("_With_Logos", "_With_Logo"):
        if folder.endswith(ending):
            folder = folder[:-len(ending)]
    return folder


def names_for(character, people):
    """The character names a picture may carry: those of its folder whose kind of person the model saw."""
    return [name for name, kind in CHARACTERS.get(character or "", []) if kind in people]


def probe_media(path):
    """(width, height, seconds or None) from ffprobe."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height:format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True)
    try:
        info = json.loads(out.stdout)
        stream = info["streams"][0]
        seconds = float(info.get("format", {}).get("duration", 0) or 0)
        return int(stream["width"]), int(stream["height"]), seconds
    except (ValueError, KeyError, IndexError):
        raise TagError(f"ffprobe can't read {path}: {out.stderr.strip()[:200]}") from None


def grab_frame(path, out, long_side, at=None):
    """One frame of a picture or video as a JPEG whose longer side is long_side."""
    scale = f"scale='if(gte(iw,ih),{long_side},-2)':'if(gte(iw,ih),-2,{long_side})':flags=lanczos"
    seek = ["-ss", f"{at:.3f}"] if at else []
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", *seek, "-i", str(path), "-frames:v", "1", "-vf", scale,
                           "-q:v", "3", str(out)], capture_output=True, text=True)
    if done.returncode or not Path(out).is_file():
        raise TagError(f"ffmpeg can't read a frame of {path}: {done.stderr.strip()[:200]}")


# --- The model ---

def clean_words(values, limit=None):
    """A list of short lowercase tags, in order, without repeats or empty ones."""
    seen, out = set(), []
    for value in values if isinstance(values, list) else []:
        word = " ".join(str(value).lower().replace("_", " ").split()).strip(" .,;:")
        if word and word not in seen:
            seen.add(word)
            out.append(word)
    return out[:limit] if limit else out


def read_reply(content):
    try:
        a = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"reply is not JSON ({e})") from None
    if not isinstance(a, dict):
        raise ValueError("reply is not a JSON object")
    return a


def parse_answer(content):
    """The model's reply to PROMPT as clean tags; ValueError if it can't be used."""
    a = read_reply(content)
    description = " ".join(str(a.get("description", "")).split())
    subjects = clean_words(a.get("subjects"), MAX_SUBJECTS)
    if not description or not subjects:
        raise ValueError("no description or no subjects")
    main = " ".join(str(a.get("main_subject", "")).lower().split()).strip(" .") or subjects[0]
    focus = []
    for key in ("focus_x", "focus_y"):
        value = a.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} is not a number")
        focus.append(round(min(1000.0, max(0.0, float(value))) / 1000, 2))   # 0-1000 is the scale qwen3-vl points in
    time_of_day = a.get("time") if a.get("time") in TIMES else "day"
    return {"description": description, "main_subject": main, "subjects": subjects,
            "setting": [s for s in clean_words(a.get("setting")) if s in SETTINGS],
            "time": time_of_day, "people": [p for p in clean_words(a.get("people")) if p in PEOPLE], "focus": focus}


def parse_text(content):
    """The model's reply to TEXT_PROMPT: {"overlay": the kinds of thing printed on the picture, "seen": its words}."""
    a = read_reply(content)
    keys = {"big_text": "big text", "logo": "logo", "rating_card": "rating card"}
    if not all(isinstance(a.get(key), bool) for key in keys):
        raise ValueError("big_text, logo and rating_card must each be true or false")
    seen = [" ".join(str(w).split()) for w in a.get("words_seen", []) if str(w).strip()][:5] \
        if isinstance(a.get("words_seen"), list) else []
    return {"overlay": [name for key, name in keys.items() if a[key]], "seen": seen}


# The two questions asked about a frame: its prompt, the answer's shape, how the answer is read, and its longest length.
QUESTIONS = {"tags": (PROMPT, SCHEMA, parse_answer, 400), "text": (TEXT_PROMPT, TEXT_SCHEMA, parse_text, 150)}


def build_request(model, image_b64, capabilities, what="tags"):
    prompt, schema, _, longest = QUESTIONS[what]
    body = {"model": model, "stream": False, "keep_alive": KEEP_ALIVE, "format": schema,
            "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
            "options": {**OPTIONS, "num_predict": longest}}
    if "thinking" in capabilities:
        body["think"] = False
    return body


def ask_model(model, jpeg, capabilities, what="tags", http=vlm.http_json):
    """One question about one frame: "tags" (what it shows) or "text" (what is printed on it). One more try with
    another seed if the reply can't be used. (answer, seconds)."""
    image = base64.b64encode(Path(jpeg).read_bytes()).decode("ascii")
    body = build_request(model, image, capabilities, what)
    started, problem = time.perf_counter(), None
    for attempt in range(2):
        body["options"]["seed"] = OPTIONS["seed"] + attempt
        try:
            reply = http(vlm.OLLAMA_URL + "/api/chat", body, timeout=CALL_TIMEOUT)
            return QUESTIONS[what][2](reply.get("message", {}).get("content", "")), time.perf_counter() - started
        except ValueError as e:
            problem = str(e)
        except (urllib.error.URLError, OSError) as e:
            raise TagError(f"Ollama stopped answering at {vlm.OLLAMA_URL} ({e})") from None
    raise ValueError(problem)


def black_times(path, limit=SCAN_LIMIT_S, step=SCAN_STEP_S):
    """The moments, step apart in the first limit seconds of a video, whose frame is almost all black."""
    done = subprocess.run(["ffmpeg", "-v", "info", "-t", f"{limit:g}", "-i", str(path), "-vf",
                           f"fps={1 / step:g},{BLACK_FILTER}", "-an", "-f", "null", "-"],
                          capture_output=True, text=True, errors="replace")
    times = set()
    for line in done.stderr.splitlines():
        if "blackframe" in line and " t:" in line:
            try:
                times.add(round(float(line.split(" t:")[1].split()[0]), 1))
            except (ValueError, IndexError):
                pass
    return times


def picture_start(path, seconds, look):
    """Where a long video's own picture starts: after an age-rating card, and after the black frames around it.
    look(at) answers the "text" question about the frame at that moment. 0 for a short loop, which has no such card."""
    if seconds <= SCAN_MIN_S:
        return 0.0
    black = black_times(path)
    limit = min(SCAN_LIMIT_S, seconds - SCAN_STEP_S)
    at = 0.0
    while at <= limit:
        if round(at, 1) not in black and "rating card" not in look(at)["overlay"]:
            return round(at, 2)
        at += SCAN_STEP_S
    return 0.0


def tag_file(rel, root, model, capabilities, work, long_side=SEND_LONG_SIDE, ask=ask_model, old=None):
    """The whole entry for one file. A video gets the tags of the first frame of its own picture and of the frame
    half way from there to its end, joined. old is the entry the file has when only what is printed on it is still
    to be asked: its tags are kept as they are."""
    path = Path(root) / rel
    width, height, seconds = probe_media(path)
    facts = folder_facts(rel)
    is_video = path.suffix.lower() in VIDEO_EXTS
    spent, grabbed = [0.0], {}

    def look(at, what):
        if at not in grabbed:
            grabbed[at] = Path(work) / f"frame_{len(grabbed)}.jpg"
            grab_frame(path, grabbed[at], long_side, at)
        answer, took = ask(model, grabbed[at], capabilities, what)
        spent[0] += took
        return answer

    if old is not None:
        entry = dict(old)
        frames = [dict(f) for f in old.get("frames", [])] or [None]
    else:
        start = picture_start(path, seconds, lambda at: look(at, "text")) if is_video else 0.0
        moments = [start, round(start + (seconds - start) / 2, 2)] if is_video else [None]
        frames = [{**({"at_s": at} if is_video else {}), **look(at, "tags")} for at in moments]
        first = frames[0]
        entry = {"kind": "video" if is_video else "image", "size": [width, height],
                 "folder": Path(rel).parent.as_posix(), "region": facts["region"],
                 "description": first["description"], "main_subject": first["main_subject"],
                 "subjects": clean_words([s for f in frames for s in f["subjects"]],
                                         MAX_SUBJECTS + 4 if is_video else MAX_SUBJECTS),
                 "setting": clean_words([s for f in frames for s in f["setting"]]),
                 "time": first["time"], "people": clean_words([p for f in frames for p in f["people"]]),
                 "focus": first["focus"]}
        entry["names"] = names_for(facts["character"], entry["people"])
        if is_video:
            entry.update(length_s=round(seconds, 2), start_s=start)
        entry.update(model=model, prompt_version=PROMPT_VERSION, tagged=date.today().isoformat(),
                     file_bytes=path.stat().st_size)
    overlay, seen = [], []
    for frame in frames:
        text = look(frame.get("at_s") if frame else None, "text")
        overlay += text["overlay"]
        seen += text["seen"]
        if frame:
            frame["overlay"] = text["overlay"]
    if "_with_logo" in rel.lower():
        overlay.append("logo")                       # the folder says so, whatever the model saw
    if is_video:
        entry["frames"] = frames
        entry.setdefault("start_s", 0.0)
    entry.update(type=media_type(rel), shape=shape_of(width, height), group=group_of(rel),
                 overlay=[o for o in OVERLAYS if o in overlay], overlay_seen=clean_words(seen, 6),
                 text_version=TEXT_VERSION, seconds=round(entry.get("seconds", 0.0) * (old is not None) + spent[0], 1))
    return entry


# --- The tags file and the override file ---

def read_json(path, what):
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig"))   # Notepad may add a byte order mark
    except ValueError as e:
        raise TagError(f"{path} ({what}) is not valid JSON: {e}") from None
    if not isinstance(data, dict):
        raise TagError(f"{path} ({what}) must hold one JSON object")
    return data


def write_json(path, data):
    """Written to a temporary file first, so a run that is stopped half way never leaves a broken file."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def clean_override(rel, fix):
    """One file's fixes from the override file, checked. TagError names what is wrong."""
    if not isinstance(fix, dict):
        raise TagError(f"override for {rel}: must be an object such as {{\"subjects\": [\"airboat\", \"swamp\"]}}")
    out = {}
    for key, value in fix.items():
        if key not in FIELDS:
            raise TagError(f"override for {rel}: {key!r} is not a tag; use {', '.join(FIELDS)}")
        if key in ("subjects", "setting", "people", "names"):
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise TagError(f"override for {rel}: {key} is a list of words")
            out[key] = [" ".join(v.split()) for v in value if v.strip()] if key == "names" else clean_words(value)
        elif key == "focus":
            good = (isinstance(value, list) and len(value) == 2
                    and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in value))
            if not good:
                raise TagError(f"override for {rel}: focus is [across, down], each from 0 to 1")
            out[key] = [round(float(v), 2) for v in value]
        elif key == "region":
            if value is not None and not isinstance(value, str):
                raise TagError(f"override for {rel}: region is a name such as Grassrivers, or null")
            out[key] = value
        elif key == "overlay":
            if not isinstance(value, list) or not all(v in OVERLAYS for v in value):
                raise TagError(f"override for {rel}: overlay lists what is printed on the picture, any of "
                               f"{', '.join(OVERLAYS)}; an empty list for nothing")
            out[key] = [o for o in OVERLAYS if o in value]
        elif key == "type":
            if value not in TYPES:
                raise TagError(f"override for {rel}: type is one of {', '.join(TYPES)}")
            out[key] = value
        elif key == "start_s":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise TagError(f"override for {rel}: start_s is the second a video's own picture starts at")
            out[key] = round(float(value), 2)
        else:
            if not isinstance(value, str) or not value.strip():
                raise TagError(f"override for {rel}: {key} is text")
            out[key] = " ".join(value.split()).lower() if key in ("main_subject", "time") else " ".join(value.split())
    return out


def apply_overrides(files, overrides):
    """The tags with the owner's fixes on top. Each fixed entry lists what was fixed in "overridden"."""
    merged = {}
    for rel, entry in files.items():
        fix = clean_override(rel, overrides[rel]) if rel in overrides else {}
        merged[rel] = {**entry, **fix, **({"overridden": sorted(fix)} if fix else {})}
    return merged


def load_tags(root=MEDIA_ROOT, tags_path=None):
    """{relative path: tags}, the override file already applied. This is what everything else reads."""
    root = Path(root)
    data = read_json(tags_path or root / TAGS_NAME, "the tags file")
    overrides = read_json(root / OVERRIDE_NAME, "the override file").get("files", {})
    return apply_overrides(data.get("files", {}), overrides)


def needs_tagging(rel, files, root, force):
    """What a file still needs: "all" (both questions), "text" (only what is printed on it; its tags are kept), or
    None. A file that was replaced by another starts again."""
    entry = files.get(rel)
    if force or entry is None or entry.get("file_bytes") != (Path(root) / rel).stat().st_size:
        return "all"
    if entry.get("text_version") != TEXT_VERSION:
        # A long video is also searched for where its picture starts, so it is tagged again from that frame.
        return "all" if entry["kind"] == "video" and entry.get("length_s", 0) > SCAN_MIN_S else "text"
    return None


# --- The check sheet ---

def thumb_name(rel, at=None):
    """A thumbnail's file name. A video frame's name holds its moment, so a frame from another moment is made anew."""
    return hashlib.sha1(rel.encode("utf-8")).hexdigest()[:12] + (f"_{round(at * 100):05d}" if at is not None else "") + ".jpg"


def build_sheet(root=MEDIA_ROOT, tags_path=None, log=print):
    """Write _tag_check/index.html and its thumbnails. Returns the page's path."""
    root = Path(root)
    check_dir = root / CHECK_DIR_NAME
    thumbs = check_dir / "thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    data = read_json(tags_path or root / TAGS_NAME, "the tags file")
    override_file = read_json(root / OVERRIDE_NAME, "the override file")
    tags = data.get("files", {})
    apply_overrides(tags, override_file.get("files", {}))       # only to stop here on a broken override file
    items, made = [], 0
    for rel in sorted(tags, key=str.lower):
        entry = tags[rel]
        moments = [f.get("at_s", 0.0) for f in entry.get("frames", [{}])] if entry["kind"] == "video" else [None]
        names = []
        for at in moments:
            name = thumb_name(rel, at)
            if not (thumbs / name).is_file() and (root / rel).is_file():
                grab_frame(root / rel, thumbs / name, THUMB_WIDTH, at)
                made += 1
            names.append("thumbs/" + name)
        # The page shows the model's tags with the owner's fixes on top, and can tell the two apart.
        items.append({"path": rel, "thumbs": names, "model": {k: entry.get(k) for k in FIELDS},
                      "kind": entry["kind"], "folder": entry["folder"], "size": entry["size"],
                      "shape": entry.get("shape"), "seen": entry.get("overlay_seen", []),
                      "frames": [{"at_s": f.get("at_s")} for f in entry.get("frames", [])]})
    used = {name.split("/")[-1] for item in items for name in item["thumbs"]}
    for old in thumbs.glob("*.jpg"):
        if old.name not in used:
            old.unlink()                             # a thumbnail of a frame or file that is no longer on the sheet
    page = PAGE_TEMPLATE.read_text(encoding="utf-8")
    payload = {"items": items, "override": override_file.get("files", {}), "model": data.get("model"),
               "made": date.today().isoformat(), "settings": list(SETTINGS), "times": list(TIMES),
               "types": list(TYPES), "overlays": list(OVERLAYS),
               "media_root": str(root), "override_name": OVERRIDE_NAME}
    page = page.replace("/*DATA*/null", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    out = check_dir / "index.html"
    out.write_text(page, encoding="utf-8")
    log(f"Check sheet: {out}  ({len(items)} files, {made} new thumbnails)")
    return out


# --- The run ---

def run(args, log=print, ask=ask_model, ollama=vlm):
    """Tag what needs it and rebuild the check sheet. ask and ollama are swapped for stand-ins by the tests."""
    root = Path(args.root)
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise TagError("ffmpeg and ffprobe must be on PATH")
    tags_path = Path(args.out) if args.out else root / TAGS_NAME
    if args.sheet:
        build_sheet(root, tags_path, log)
        return 0
    wanted = [rel for rel in media_files(root) if not args.only or any(o.lower() in rel.lower() for o in args.only)]
    if not wanted:
        raise TagError(f"no pictures or videos found under {root} ({', '.join(FOLDERS)})"
                       + (f" matching {args.only}" if args.only else ""))
    data = read_json(tags_path, "the tags file")
    files = data.setdefault("files", {})
    needs = {rel: needs_tagging(rel, files, root, args.force) for rel in wanted}
    todo = [rel for rel in wanted if needs[rel]]
    only_text = sum(1 for rel in todo if needs[rel] == "text")
    log(f"{len(wanted)} files, {len(wanted) - len(todo)} already tagged, {len(todo)} to tag with {args.model}"
        + (f" ({only_text} of them only need the check for text and logos)" if only_text else ""))
    failed = []
    if todo:
        try:
            info = ollama.check_ollama(args.model)
        except vlm.VlmError as e:
            raise TagError(str(e)) from None
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="tag_") as work:
            for n, rel in enumerate(todo, 1):
                try:
                    entry = tag_file(rel, root, args.model, info["capabilities"], work, args.long_side, ask,
                                     files[rel] if needs[rel] == "text" else None)
                except ValueError as e:
                    failed.append(rel)
                    log(f"[{n:3}/{len(todo)}] {rel}: NOT TAGGED, the model's answer couldn't be used ({e})")
                    continue
                files[rel] = entry
                data.update(model=args.model, prompt_version=PROMPT_VERSION)
                write_json(tags_path, data)
                log(f"[{n:3}/{len(todo)}] {rel}  {entry['seconds']:.1f} s  {entry['type']}, {entry['shape']}, "
                    f"on it: {', '.join(entry['overlay']) or 'nothing'}  |  {entry['main_subject']}: "
                    f"{', '.join(entry['subjects'])}")
        memory = ollama.model_memory(args.model)
        if memory and memory.get("size_bytes"):
            log(f"Model in memory: {memory['size_bytes'] / 1e9:.1f} GB, {memory['vram_bytes'] / 1e9:.1f} GB of it on the GPU")
        ollama.unload(args.model)
        log(f"Tagged {len(todo) - len(failed)} files in {time.perf_counter() - started:.0f} s; the model is unloaded")
    gone = [rel for rel in files if not (root / rel).is_file()]
    if gone and not args.out:
        log(f"Note: {len(gone)} tagged file(s) are no longer on disk and stay in the tags file: {', '.join(gone[:3])}")
    if not args.out:
        overrides = read_json(root / OVERRIDE_NAME, "the override file").get("files", {})
        unknown = [rel for rel in overrides if rel not in files]
        if unknown:
            log(f"WARNING: the override file names {len(unknown)} file(s) that have no tags: {', '.join(unknown[:3])}")
        build_sheet(root, tags_path, log)
    if failed:
        log(f"WARNING: {len(failed)} file(s) were not tagged; run again, or add them to {OVERRIDE_NAME}")
        return 1
    return 0


def main():
    sys.stdout.reconfigure(errors="replace", line_buffering=True)    # each file's line shows as soon as it is tagged
    parser = argparse.ArgumentParser(description="Tag the GTA 6 pictures and video loops by what they show.")
    parser.add_argument("--root", type=Path, default=MEDIA_ROOT, help="the media folder (default C:\\AI\\gta\\rockstar)")
    parser.add_argument("--model", default=MODEL, help=f"the Ollama vision model (default {MODEL})")
    parser.add_argument("--force", action="store_true", help="tag again even if a file already has tags")
    parser.add_argument("--only", action="append", help="only files whose path holds this text; may be repeated")
    parser.add_argument("--sheet", action="store_true", help="only rebuild the check sheet from the saved tags")
    parser.add_argument("--out", help="write the tags to this file instead (for comparing models; no check sheet)")
    parser.add_argument("--long-side", type=int, default=SEND_LONG_SIDE,
                        help=f"the size the model sees, in pixels on the longer side (default {SEND_LONG_SIDE})")
    args = parser.parse_args()
    try:
        sys.exit(run(args))
    except TagError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
