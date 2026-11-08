"""Turn a script file into a finished 1080x1920 explainer Short: Kokoro voice, captions lit word by word, a ducked music bed,
sound effects, fast cuts and a loop ending.

A script is a JSON file (see examples/nov10_preload.json). Each line says something over one or more shots, which share
that line's time by their weights (equal unless a shot says otherwise). A shot is a video, an image or a text card,
written as a short string ("rockstar/x.mp4@0:42", "rockstar/x.jpg@0.7", "card:SOME TEXT") or as an object with more
choices: where the subject is ("focus"), a slide across the whole picture ("pan") and a bigger share ("weight"). The
README lists them all. Everything is checked first, so a missing file or a bad setting stops before anything renders.

Writes short.mp4, voice.wav, captions.ass and log.json to C:\\AI\\gta\\explainers\\<slug>\\<YYYYMMDD-HHMMSS>\\.

Needs .venv-tts (requirements-explainer.txt), the two Kokoro model files and ffmpeg/ffprobe on PATH; see the README.

Usage:  .venv-tts\\Scripts\\python.exe explainer.py script.json [--root FOLDER] [--out FOLDER]
"""

import argparse
import concurrent.futures
import difflib
import functools
import json
import math
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from datetime import datetime
from pathlib import Path

from hooks import ass_time, escape_ass

HERE = Path(__file__).resolve().parent
ROOT = Path(r"C:\AI\gta")
MODEL_DIR = ROOT / "models" / "kokoro"
MODEL, VOICES = MODEL_DIR / "kokoro-v1.0.onnx", MODEL_DIR / "voices-v1.0.bin"
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1"
FONT_FILE, FONT = HERE / "fonts" / "Anton-Regular.ttf", "Anton"
FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")

W, H, FPS = 1080, 1920, 30
MIN_TOTAL_S, MAX_TOTAL_S = 40, 50    # the target runtime; over the top of it warns
DEFAULT_VOICE, DEFAULT_SPEED = "am_puck", 1.0
MAX_SPEED = 1.1                      # faster than this warns: the length is fixed by cutting words, not by rushing them
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Voice. The model leaves 0.2-0.3 s of near silence after every sentence even with its own trim on, so the tail is cut
# here. -45 dB keeps a soft ending: at -40 dB the "th" of "myth" lost 80 ms.
TAIL_DB, TAIL_KEEP_S, TAIL_FADE_S, GAP_S = -45, 0.04, 0.02, 0.12
BURST_GAP, BURST_LONG, BURST_DB = 7, 10, -30  # 10 ms frames: this far from the speech, shorter and fainter than this: not speech
TARGET_LUFS, LUFS_TOLERANCE = -14.0, 0.3
LIMIT = 0.84                         # about -1.5 dB, the ceiling of the voice and of the final mix
VOICE_HIGHPASS_HZ = 50               # the voice has almost nothing under this (about -30 dB)
NOT_A_SOUND = set(' ;:,.!?¡¿—…"«»“”()ˈˌ')  # in the phonemes, but nothing to align words on
MIN_MATCH = 0.6                      # below this the phonemes of the words and the sentence are too unlike to trust
ENGLISH = ("af_", "am_", "bf_", "bm_")  # Kokoro's US and UK voices; the rest speak other languages

# Sound. Everything is mixed at 48 kHz in numpy, then the mix is brought to -14 LUFS with the voice's limiter.
RATE = 48000
F32 = ["-f", "f32le", "-ar", str(RATE), "-ac", "2"]  # how ffmpeg reads the raw stereo float files
MUSIC_DIR = "music"                  # inside the root: where "auto" picks from, and where a bare name is looked up first
MUSIC_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac"}
# The music sits 16 LU under the voice while it speaks and rises GAP_LIFT_DB in a pause of LIFT_GAP_S or more. Kokoro's
# sentence breaks are about 0.2 s, so it doesn't pump on each one. The whole voice is known, so the dip can look ahead:
# it starts DUCK_LEAD_S before speech returns and is all the way down by then.
MUSIC_LUFS, GAP_LIFT_DB, LIFT_GAP_S = -30.0, 4.0, 0.3
SPEECH_DB = -40                      # 10 ms of the voice this far under its loudest moment is a pause
DUCK_LEAD_S, DUCK_HOLD_S = 0.08, 0.06
DUCK_DOWN_S, DUCK_UP_S = 0.06, 0.25  # how long a whole dip and a whole rise take
MUSIC_FADE_IN_S, MUSIC_FADE_OUT_S = 0.5, 1.0
MUSIC_PRESENCE = "equalizer=f=2500:t=q:w=1:g=-3"  # a small dip in the music where the voice's consonants are
MAX_MUSIC_VOLUME = 2.0               # music_volume: 1 is the level above, 0.5 is 6 dB quieter, 2 is 6 dB louder
# Sound effects, made here from noise and sine waves. A whoosh peaks on the cut when a line starts or a pan begins; a
# hit lands on a card. Not on every cut. All are cut above 8 kHz: they don't need it, and it keeps them off the voice's
# "s" sounds. The levels are measured as loudness under the voice (explainertest.py checks them).
WHOOSH_S, WHOOSH_PEAK_S, WHOOSH_DB = 0.6, 0.36, -14.5   # length, where its loudest moment is, peak level
HIT_S, HIT_DB = 0.5, -15.0
SFX_LOWPASS_HZ, SFX_SPACING_S, SFX_EARLIEST_S = 8000, 0.3, 0.1

# Captions. Shorts covers the top 380, bottom 380, right 120 and left 60 px (the same zones as hooks.py), so x centres
# on 510. Anton has one weight, so bold stays 0: bold would ask libass for a faked one.
CENTRE_X, SAFE_LEFT, SAFE_RIGHT = 510, 60, 120
# libass sizes a font by its full line height, and Anton's is tall: at size 176 a capital is only about 45 px wide.
CAPTION_SIZE, CAPTION_OUTLINE, CAPTION_BOTTOM = 176, 12, 1500
CAPTION_CHARS, GROUP_CHARS = 16, 28  # most characters on a line, then in a chunk (which may take 2 lines)
MIN_WORDS, MAX_WORDS, CAPTION_LINGER_S = 2, 4, 0.25
TITLE_SIZE, TITLE_OUTLINE, TITLE_CHARS, OPEN_CARD_S = 260, 14, 13, 2.0
OPEN_CARD_Y, CARD_Y = 650, 720
WHITE = "&HFFFFFF&"
COLOURS = {"green": "&H14FF39&", "red": "&H2A2AFF&"}  # ASS colours are blue-green-red: #39FF14 and #FF2A2A
HIGHLIGHT = "&H00E6FF&"              # #FFE600: the word being said. Tagged words keep their green or red.
# A soft shadow under the captions' black outline, so they also read on a bright, busy picture: px, and ASS alpha
# (00 solid, FF clear).
CAPTION_SHADOW, SHADOW_ALPHA = 5, "70"
POP = "\\fscx88\\fscy88\\t(0,70,\\fscx106\\fscy106)\\t(70,130,\\fscx100\\fscy100)"
OPEN_POP = POP + "\\fad(0,120)"
CARD_COLOUR = "0x12121C"
# Card markup: | starts a new line, ~words~ get a red bar through them, *words* are green. Anton measured with libass:
# a capital is 0.5 of the size tall, its middle sits 0.06 of the size below the line's anchor, lines are 0.98 apart, and
# each glyph's advance is below as a share of the size (0.28 for the rest). The bar is drawn, because libass's own
# strikeout is a hairline at the height of a lowercase letter, which Anton's tall capitals hide.
CARD_MARKS, MAX_CARD_LINES = "~*", 3
CAP_MIDDLE, LINE_STEP = 0.06, 0.98
GLYPH_WIDTHS = {"M": 0.43, "W": 0.41, "1": 0.195, "J": 0.2, "-": 0.18, **{c: 0.135 for c in "I.,:;'!| "}}
BAR_HEIGHT, BAR_PAD, BAR_OUTLINE, BAR_TILT = 0.08, 0.1, 6, 3   # of the size, of the size each side, px, degrees
BAR_DELAY_MS, BAR_GROW_MS = 250, 200                           # the old text shows first, then the bar strikes it

# Timeline.
LOOP_FRAMES = 45                     # the last 1.5 s shows the first visual again
MIN_SLOT_FRAMES = 12                 # a visual left with less than this before the loop ending is dropped
LONG_SLOT_S, LONG_HOLD_S = 2.5, 1.0  # warn: the target is a new picture every 1.5-2 s, and a long freeze looks stuck
LONG_PAN_S = 3.5                     # a pan keeps moving, so it may stay up longer
FAST_PAN = 1.2                       # screen widths a second; faster than this a pan smears into a whip
MAX_WEIGHT = 5.0
CLEAN_SEAM_DB = 30                   # a clip's last and first frame must be this alike to loop it; else it holds

# Framing. focus is where the subject is across the picture, 0 (left edge) to 1 (right edge); the 9:16 crop centres on it
# as far as the picture allows. [x, y] adds how far down it is, 0 (top) to 1 (bottom), so a zoom keeps a face that sits
# near the top. The names are the middles of the picture's thirds.
FOCUS_NAMES = {"left": 1 / 6, "center": 0.5, "centre": 0.5, "right": 5 / 6}
FOCUS_Y_NAMES = {"top": 1 / 6, "middle": 0.5, "center": 0.5, "centre": 0.5, "bottom": 5 / 6}
PAN_DIRECTIONS = {"right": (0.0, 1.0), "left": (1.0, 0.0)}   # right: from the left edge to the right edge
SHOT_KEYS = {"file", "card", "at", "focus", "pan", "weight"}
SCRIPT_KEYS = {"title", "voice", "speed", "open_card", "music", "music_volume", "sfx", "lines"}
OFF_CENTRE = 0.1                     # a subject further than this from the crop's middle gets zooms only, never a drift
PAN_BLUR = 0.14                      # a pan's motion blur, as a share of its step per frame: a camera's half-open shutter

# Ken Burns. zoompan moves its window in whole pixels, so the picture is enlarged first and a pixel of the window is
# about a third of a pixel on screen. Without that the motion visibly shakes.
CANVAS_SCALE = 3
MOTIONS = ["zoom_in", "zoom_out", "pan_left", "pan_right"]
SPEED_RANGE = (0.8, 1.25)            # how much faster or slower than the base rates each one runs
ZOOM_PER_S, ZOOM_MAX = 0.05, 0.25
PAN_PX_PER_S, PAN_MAX_PX = 90, 250

# Scale filters and output tags: photos are full-range BT.601 and must not come out with shifted colours.
COLOUR_OUT = "out_color_matrix=bt709:out_range=tv"
COLOUR_TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


class ScriptError(Exception):
    """Something the owner has to fix. The message says what, and nothing further has been rendered."""


def ffmpeg(args, cwd=None, loglevel="error"):
    r = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-loglevel", loglevel, "-y", *map(str, args)], cwd=cwd,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise ScriptError(f"ffmpeg failed: {r.stderr.strip()[-800:]}")
    return r.stderr


@functools.lru_cache(maxsize=None)
def probe(path):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return json.loads(r.stdout) if r.returncode == 0 else {}


def stream_of(info, kind):
    return next((s for s in info.get("streams", []) if s.get("codec_type") == kind), None)


# --- Reading and checking the script ---

def parse_time(text):
    """'42', '42.5', '0:42' or '1:02:03' in seconds, or None if it is none of those."""
    if not re.fullmatch(r"\d+(\.\d+)?|\d+:[0-5]?\d(\.\d+)?|\d+:[0-5]?\d:[0-5]?\d(\.\d+)?", text):
        return None
    seconds = 0.0
    for part in text.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds


def parse_share(value, names):
    """A name from names, a number from 0 to 1, or a string holding one; None if it is none of those."""
    if isinstance(value, str) and value.strip().lower() in names:
        return names[value.strip().lower()]
    if isinstance(value, str) and re.fullmatch(r"0(\.\d+)?|1(\.0+)?", value.strip()):
        return float(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1:
        return float(value)
    return None


def parse_focus(value, where):
    """A focus as (across, down), each 0 to 1. Across alone leaves down in the middle."""
    if isinstance(value, list) and len(value) == 2:
        x, y = parse_share(value[0], FOCUS_NAMES), parse_share(value[1], FOCUS_Y_NAMES)
    else:
        x, y = parse_share(value, FOCUS_NAMES), 0.5
    if x is None or y is None:
        raise ScriptError(f"{where}: focus is where the subject is: left, center, right or 0 (left edge) to 1 (right "
                          "edge), or [across, down] with down top, middle, bottom or 0 (top) to 1 (bottom)")
    return x, y


def card_lines(text):
    """A card's text as lines of (words, style) pieces, style None, "strike" or "green". ScriptError if the marks don't
    pair up. Without any mark or |, it is one line and may still be broken in two to fit."""
    lines = []
    for raw_line in text.split("|"):
        pieces, style, current = [], None, ""
        for c in raw_line:
            if c in CARD_MARKS:
                mark = "strike" if c == "~" else "green"
                if style not in (None, mark):
                    raise ScriptError(f"card {text!r}: a ~ or * opens inside the other")
                if current:
                    pieces.append((current, style))
                current, style = "", (None if style == mark else mark)
            else:
                current += c
        if style is not None:
            raise ScriptError(f"card {text!r}: a ~ or * isn't closed on its own line")
        if current:
            pieces.append((current, style))
        pieces = [(t, s) for t, s in pieces if t.strip() or s is None]
        if not "".join(t for t, _ in pieces).strip():
            raise ScriptError(f"card {text!r}: a line with no text")
        lines.append(pieces)
    if len(lines) > MAX_CARD_LINES:
        raise ScriptError(f"card {text!r}: at most {MAX_CARD_LINES} lines")
    return lines


def parse_card(text, source, weight):
    text = text.strip().upper()
    if not text:
        raise ScriptError("a card with no text")
    card_lines(text)
    visual = {"source": source, "kind": "card", "text": text}
    if weight != 1.0:
        visual["weight"] = weight
    return visual


def parse_file(name, at, focus, pan, weight, root, source):
    """An image or video, checked against the real file. at is a video's start; focus and pan are already read."""
    path = root / name
    if not path.is_file():
        raise ScriptError(f"file not found: {path}")
    ext = path.suffix.lower()
    info = probe(path)
    picture = stream_of(info, "video")
    if ext not in VIDEO_EXTS | IMAGE_EXTS:
        raise ScriptError(f"{path.name}: {ext or 'no extension'} is not a video or image type this tool handles")
    if picture is None:
        raise ScriptError(f"{path.name}: ffprobe finds no picture in it")
    size = (int(picture["width"]), int(picture["height"]))
    shape = {"weight": weight} if weight != 1.0 else {}
    across, down = focus or (0.5, 0.5)
    if ext in IMAGE_EXTS:
        if at is not None:
            raise ScriptError(f"{source}: at is a video's start time; an image has none")
        if pan is not None and size[0] * H <= size[1] * W:
            raise ScriptError(f"{source}: pan slides across a wide picture, and this one is no wider than the screen")
        return {"source": source, "kind": "image", "path": path, "focus": across, "focus_y": down,
                "pan": pan, "size": size, **shape}
    if pan is not None:
        raise ScriptError(f"{source}: pan is for images; a video keeps its own movement")
    if "duration" not in info.get("format", {}):
        raise ScriptError(f"{path.name}: ffprobe can't read its length")
    length = float(info["format"]["duration"])
    start = 0.0
    if at is not None:
        start = parse_time(at)
        if start is None:
            raise ScriptError(f"{source}: bad time {at!r}, use 42, 0:42 or 1:02:03")
    if start >= length:
        raise ScriptError(f"{source}: starts at {start:.1f} s but the video is only {length:.1f} s long")
    frames = str(picture.get("nb_frames", ""))
    frames = int(frames) if frames.isdigit() else round(length * FPS)
    return {"source": source, "kind": "video", "path": path, "start": start, "length": length, "frames": frames,
            "focus": across, "focus_y": down, "size": size, **shape}


def parse_visual(raw, root):
    """One shot as a dict, checked against the real file. ScriptError says what is wrong.

    A string is the short form: "card:TEXT", "video@start" or "image@focus". An object has "file" or "card", and may
    add "at" (a video's start), "focus", "pan" (an image only, "left" or "right") and "weight"."""
    if isinstance(raw, dict):
        return parse_shot_object(raw, root)
    if not isinstance(raw, str) or not raw.strip():
        raise ScriptError(f"{raw!r} is not a visual")
    if raw.startswith("card:"):
        return parse_card(raw[5:], raw, 1.0)
    name, at = raw, None
    if "@" in raw and not (root / raw).exists():  # a file name may hold an @ of its own
        name, _, at = raw.rpartition("@")
    if at is not None and Path(name).suffix.lower() in IMAGE_EXTS:
        try:
            focus = parse_focus(at, raw)
        except ScriptError:
            raise ScriptError(f"{raw}: after an image's @ goes where the subject is: left, center, right or "
                              "0 (left edge) to 1 (right edge)") from None
        return parse_file(name, None, focus, None, 1.0, root, raw)
    return parse_file(name, at, None, None, 1.0, root, raw)


def parse_shot_object(raw, root):
    unknown = sorted(k for k in raw if k not in SHOT_KEYS)
    if unknown:
        raise ScriptError(f"{json.dumps(raw)}: unknown setting {', '.join(map(repr, unknown))}")
    if ("file" in raw) == ("card" in raw):
        raise ScriptError(f"{json.dumps(raw)}: a shot has a file or a card, one of the two")
    weight = raw.get("weight", 1.0)
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 < weight <= MAX_WEIGHT:
        raise ScriptError(f"{json.dumps(raw)}: weight is its share of the line, a number above 0 and up to {MAX_WEIGHT:g}")
    weight = float(weight)
    if "card" in raw:
        extra = sorted(k for k in raw if k not in {"card", "weight"})
        if extra:
            raise ScriptError(f"{json.dumps(raw)}: a card takes only its text and a weight, not {', '.join(extra)}")
        if not isinstance(raw["card"], str):
            raise ScriptError(f"{json.dumps(raw)}: card is its text")
        return parse_card(raw["card"], "card:" + raw["card"].strip(), weight)
    name = raw["file"]
    if not isinstance(name, str) or not name.strip():
        raise ScriptError(f"{json.dumps(raw)}: file is a path such as rockstar/x.jpg")
    source = name
    at = raw.get("at")
    if at is not None:
        at = str(at)
        source = f"{name}@{at}"
    focus = parse_focus(raw["focus"], name) if "focus" in raw else None
    pan = raw.get("pan")
    if pan is not None:
        if pan not in PAN_DIRECTIONS:
            raise ScriptError(f"{name}: pan is right (from the left edge to the right edge) or left (the other way)")
        if focus is not None:
            raise ScriptError(f"{name}: a pan crosses the whole picture, so it takes no focus")
        source = f"{name} (pan {pan})"
    elif focus is not None:
        shown = raw["focus"] if not isinstance(raw["focus"], list) else ",".join(map(str, raw["focus"]))
        source = f"{source}, focus {shown}" if at is not None else f"{name}@{shown}"
    return parse_file(name, at, focus, pan, weight, root, source)


def word_key(token):
    """A word as tags compare it: capitals, with the punctuation around it gone."""
    return token.strip(".,;:!?\"'()[]…—–“”‘’").upper()


def spoken_tokens(text):
    """The whitespace-separated pieces of the text that hold a letter or digit. A lone dash is not a word."""
    return [t for t in text.split() if any(c.isalnum() for c in t)]


def find_phrase(keys, phrase):
    """Every place the phrase's words sit in a row in keys, as start indexes."""
    want = [word_key(t) for t in phrase.split()]
    return [i for i in range(len(keys) - len(want) + 1) if want and keys[i:i + len(want)] == want]


def load_script(path, root):
    """The script with its visuals parsed. Every problem is listed at once, so a fix doesn't uncover the next one."""
    try:
        script = json.loads(Path(path).read_bytes().decode("utf-8-sig"))  # Notepad may add a byte order mark
    except OSError as e:
        raise ScriptError(f"can't read the script: {e}") from None
    except ValueError as e:
        raise ScriptError(f"{path} is not valid JSON: {e}") from None
    if not isinstance(script, dict):
        raise ScriptError(f"{path} must hold one JSON object")
    problems = [f"unknown setting {k!r}" for k in script if k not in SCRIPT_KEYS]

    title = script.get("title")
    if not isinstance(title, str) or not re.search(r"[A-Za-z0-9]", title):
        problems.append("title is missing, or has no letters or digits for the folder name")
    voice = script.get("voice", DEFAULT_VOICE)
    if not isinstance(voice, str):
        problems.append("voice must be a voice name such as am_puck")
    elif kokoro_voices() and voice not in kokoro_voices():
        english = [v for v in kokoro_voices() if v.startswith(ENGLISH)]
        problems.append(f"voice {voice!r} doesn't exist. The English voices: {', '.join(english)}")
    speed = script.get("speed", DEFAULT_SPEED)
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0.5 <= speed <= 2.0:
        problems.append("speed must be a number from 0.5 to 2.0")
    open_card = script.get("open_card")
    if open_card is not None and (not isinstance(open_card, str) or not open_card.strip()):
        problems.append("open_card must be text, or null for none")
    elif open_card is not None:
        try:
            card_lines(open_card.strip().upper())
        except ScriptError as e:
            problems.append(f"open_card: {e}")
    music, music_problem = parse_music(script.get("music"), root)
    if music_problem:
        problems.append(music_problem)
    music_volume = script.get("music_volume", 1.0)
    if (isinstance(music_volume, bool) or not isinstance(music_volume, (int, float))
            or not 0 < music_volume <= MAX_MUSIC_VOLUME):
        problems.append(f"music_volume is how loud the music is next to normal: 1 is normal, 0.5 quieter, "
                        f"up to {MAX_MUSIC_VOLUME:g} louder")
    sfx = script.get("sfx", True)
    if not isinstance(sfx, bool):
        problems.append("sfx is true (whooshes and hits) or false (none)")

    lines = script.get("lines")
    if not isinstance(lines, list) or not lines:
        problems.append("lines is missing or empty")
        lines = []
    parsed = []
    for n, line in enumerate(lines, 1):
        if not isinstance(line, dict):
            problems.append(f"line {n}: must be an object with say and visuals")
            continue
        problems += [f"line {n}: unknown setting {k!r}" for k in line if k not in {"say", "visuals", "green", "red"}]
        say = line.get("say")
        if not isinstance(say, str) or not spoken_tokens(say):
            problems.append(f"line {n}: say is missing or has nothing to speak")
            say = ""
        raws = line.get("visuals")
        if not isinstance(raws, list) or not raws:
            problems.append(f"line {n}: needs at least one visual")
            raws = []
        visuals = []
        for raw in raws:
            try:
                visuals.append(parse_visual(raw, root))
            except ScriptError as e:
                problems.append(f"line {n}: {e}")
        keys = [word_key(t) for t in spoken_tokens(say)]
        tags = {}
        for colour in COLOURS:
            tags[colour] = line.get(colour) or []
            if not isinstance(tags[colour], list) or not all(isinstance(t, str) for t in tags[colour]):
                problems.append(f"line {n}: {colour} must be a list of words")
                tags[colour] = []
            problems += [f"line {n}: {colour} tag {t!r} isn't in what this line says" for t in tags[colour]
                         if say and not find_phrase(keys, t)]
        parsed.append({"say": say, "visuals": visuals, "tags": tags})
    if problems:
        raise ScriptError("the script needs fixing:\n  " + "\n  ".join(problems))
    return {"title": title, "slug": re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"), "voice": voice, "speed": speed,
            "open_card": open_card.strip().upper() if open_card else None, "music": music,
            "music_volume": float(music_volume), "sfx": sfx, "lines": parsed}


def find_music(root, name):
    """A named music file: in the music folder first, then anywhere under the root. None if it is in neither."""
    for path in (root / MUSIC_DIR / name, root / name):
        if path.is_file():
            return path
    return None


def parse_music(value, root):
    """The music setting as None (no music), "auto" or a file's path, and a problem to report (or None)."""
    if value is None or value is False:
        return None, None
    if isinstance(value, str) and value.strip().lower() == "auto":
        return "auto", None
    if not isinstance(value, str) or not value.strip():
        return None, f'music is a file in {root / MUSIC_DIR}, "auto" to pick one from there, or null for none'
    path = find_music(root, value.strip())
    if path is None:
        return None, f"music file not found: {value!r} is in neither {root / MUSIC_DIR} nor {root}"
    if stream_of(probe(path), "audio") is None:
        return None, f"{path.name}: ffprobe finds no audio in it"
    return path, None


@functools.lru_cache(maxsize=None)
def kokoro_voices():
    """The voice names in Kokoro's voices file, or none if it's missing (load_kokoro says so then)."""
    import numpy as np
    if not VOICES.is_file():
        return ()
    with np.load(VOICES) as voices:
        return tuple(sorted(voices.files))


def music_tracks(root):
    """Every file in the music folder that has audio, by name."""
    folder = root / MUSIC_DIR
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in MUSIC_EXTS
                   and stream_of(probe(p), "audio") is not None), key=lambda p: p.name.lower())


def pick_music(choice, root, seconds, seed):
    """The track to use, and how it was chosen. "auto" picks from the music folder, the same one each time for the same
    title, and prefers a track long enough to play without looping. None if there is no music."""
    if choice != "auto":
        return choice, ("named in the script" if choice else None)
    tracks = music_tracks(root)
    if not tracks:
        return None, None
    long_enough = [p for p in tracks if float(probe(p).get("format", {}).get("duration", 0)) >= seconds]
    pool = long_enough or tracks
    return random.Random(seed).choice(pool), f"picked automatically from {len(tracks)} track(s)"


def script_warnings(script):
    warnings = [f"line {n}: espeak reads ${m} as \"dollar {m}\", so write \"{m} dollars\" instead"
                for n, line in enumerate(script["lines"], 1) for m in re.findall(r"\$(\d[\d,.]*\d|\d)", line["say"])]
    speed = script.get("speed", DEFAULT_SPEED)
    if speed > MAX_SPEED:
        warnings.append(f"speed {speed:g} is faster than {MAX_SPEED:g}: to fit the time, cut words instead")
    return warnings


# --- Voice and word timing ---

def load_kokoro():
    try:
        from kokoro_onnx import Kokoro
    except ImportError:
        raise ScriptError("kokoro-onnx isn't installed for this Python. Run explainer.py with "
                          ".venv-tts\\Scripts\\python.exe; the README shows how to set it up.") from None
    missing = [str(p) for p in (MODEL, VOICES) if not p.is_file()]
    if missing:
        raise ScriptError(f"model file not found: {', '.join(missing)}. Download it from {MODEL_URL}")
    kokoro = Kokoro(str(MODEL), str(VOICES))
    if not kokoro.has_timings:
        raise ScriptError(f"{MODEL} reports no durations, so captions can't be timed. It's the older file of the same "
                          f"name: download kokoro-v1.0.onnx from {MODEL_URL}")
    return kokoro


def split_sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if spoken_tokens(s)]


def word_starts(word_phonemes, spoken):
    """Where each word starts in spoken, the sentence's phonemes one by one, or None if they can't be lined up.

    The sentence is voiced whole, because phonemizing word by word changes the speech ("a" becomes "ay", and "to be"
    gets the wrong stress). The words' own phonemes are used only to find the joins. They don't have to match:
    "of the" is one group in the sentence but two words, and "212" is two groups for one word."""
    keep = [i for i, p in enumerate(spoken) if p not in NOT_A_SOUND]
    words = ["".join(c for c in w if c not in NOT_A_SOUND) for w in word_phonemes]
    if not keep or not all(words):
        return None
    joined, sounds = "".join(words), "".join(spoken[i] for i in keep)
    matcher = difflib.SequenceMatcher(None, joined, sounds, autojunk=False)
    if matcher.ratio() < MIN_MATCH:
        return None
    where = [0] * len(joined)
    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        for a in range(a1, a2):
            # Equal: exact. Replaced: spread evenly over what was said instead. Not said: the next sound that was.
            where[a] = b1 + (a - a1 if tag == "equal" else (a - a1) * (b2 - b1) // (a2 - a1))
    starts, at = [], 0
    for word in words:
        starts.append(keep[min(where[at], len(keep) - 1)])
        at += len(word)
    return starts if all(a < b for a, b in zip(starts, starts[1:])) else None


def speech_end(audio, rate):
    """The sample where the speech has really finished, plus a little.

    The model often adds a faint burst 70-230 ms after the speech, which would keep all that silence in if the cut went
    by the last sound alone. So faint short bursts that far from the sound before them are walked back over. A real soft
    ending stays: the "th" of "myth" and the "s" of "FPS" sit within 60 ms of the sound before them."""
    import numpy as np
    frame = int(0.01 * rate)
    count = len(audio) // frame
    if not count:
        return len(audio)
    rms = np.sqrt((audio[:count * frame].reshape(-1, frame) ** 2).mean(1))
    loud = np.nonzero(rms >= rms.max() * 10 ** (TAIL_DB / 20))[0].tolist()
    while True:
        first = len(loud) - 1
        while first > 0 and loud[first] - loud[first - 1] < BURST_GAP:
            first -= 1
        is_faint = rms[loud[first:]].max() < rms.max() * 10 ** (BURST_DB / 20)
        if first == 0 or loud[-1] - loud[first] >= BURST_LONG or not is_faint:
            return min(len(audio), (loud[-1] + 1) * frame + int(TAIL_KEEP_S * rate))
        del loud[first:]


def longest_quiet(audio, rate, db=TAIL_DB):
    """The longest stretch below db under the loudest moment, in seconds: the dead air left."""
    import numpy as np
    frame = int(0.01 * rate)
    count = len(audio) // frame
    rms = np.sqrt((audio[:count * frame].reshape(-1, frame) ** 2).mean(1))
    longest = run = 0
    for quiet in rms < rms.max() * 10 ** (db / 20):
        run = run + 1 if quiet else 0
        longest = max(longest, run)
    return longest * 0.01


def speak_sentence(kokoro, sentence, voice, speed):
    """The sentence's audio, its words' times in seconds from its start, and whether those are the model's own."""
    import numpy as np
    lang = "en-gb" if voice.startswith("b") else "en-us"
    audio, rate, spoken = kokoro.create_timed(sentence, voice, speed=speed, lang=lang,
                                              sentence_pause=0.0, clause_pause=0.0)
    end = speech_end(audio, rate)
    fade = min(int(TAIL_FADE_S * rate), end)
    audio = audio[:end].copy()
    if fade:
        audio[-fade:] *= np.linspace(1, 0, fade, dtype=audio.dtype)
    length = len(audio) / rate
    tokens = spoken_tokens(sentence)
    starts = word_starts([kokoro.tokenizer.phonemize(t, lang) for t in tokens], [t.phoneme for t in spoken])
    if starts is None:  # the owner's fallback: split the sentence's time across its words by their length
        shares = [0.0]
        for t in tokens:
            shares.append(shares[-1] + len(t))
        times = [(length * a / shares[-1], length * b / shares[-1]) for a, b in zip(shares, shares[1:])]
    else:
        ends = [spoken[b - 1].end for b in starts[1:]] + [length]
        times = [(min(spoken[a].start, length), min(e, length)) for a, e in zip(starts, ends)]
    return audio, rate, tokens, times, starts is not None


def synthesize(kokoro, lines, voice, speed):
    """The whole voice, one sentence at a time with a short gap, so the model's long pauses go away.

    Returns the samples, their rate, every word with its time, when each line starts, and how many sentences had to
    use the length split."""
    import numpy as np
    parts, words, line_starts = [], [], []
    samples, sentence_id, splits, rate = 0, 0, 0, 24000
    for line in lines:
        first_word = len(words)
        for sentence in split_sentences(line["say"]):
            audio, rate, tokens, times, exact = speak_sentence(kokoro, sentence, voice, speed)
            splits += not exact
            if parts:
                parts.append(np.zeros(int(GAP_S * rate), dtype=audio.dtype))
                samples += len(parts[-1])
            for token, (start, end) in zip(tokens, times):
                words.append({"text": token.strip(".,;:\"'()[]…—–“”‘’").upper(), "key": word_key(token),
                              "start": samples / rate + start, "end": samples / rate + end, "sentence": sentence_id,
                              "pause": token[-1] in ",;:—", "colour": None, "tag": None})
            parts.append(audio)
            samples += len(audio)
            sentence_id += 1
        apply_tags(words[first_word:], line["tags"])
        line_starts.append(words[first_word]["start"] if line_starts else 0.0)
    return np.concatenate(parts), rate, words, line_starts, splits


def save_wav(path, audio, rate):
    import numpy as np
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())


def measure_loudness(path, fmt=()):
    """Integrated loudness (LUFS) and true peak (dB) of a file. fmt says how to read a raw one (F32)."""
    err = ffmpeg([*fmt, "-i", path, "-vn", "-af", "loudnorm=print_format=json", "-f", "null", "-"], loglevel="info")
    m = json.loads(err[err.rindex("{"):err.rindex("}") + 1])
    return float(m["input_i"]), float(m["input_tp"])


def normalise(inputs, out, level, shape=""):
    """The input at about -14 LUFS in a 48 kHz stereo WAV, by gain and a limiter. Two-pass loudnorm falls back to its
    dynamic mode on this voice (the peaks leave no room) and lands near -15. Each pass starts again from the input, so
    the limiter runs only once. level is the input's loudness; shape is any filter to run first."""
    gain = 0.0
    for _ in range(4):
        gain += TARGET_LUFS - level
        ffmpeg([*inputs, "-af", f"{shape}volume={gain:.2f}dB,"
                f"alimiter=limit={LIMIT}:level=disabled:attack=3:release=40:latency=1", "-c:a", "pcm_s16le", out])
        level = measure_loudness(out)[0]
        if abs(TARGET_LUFS - level) <= LUFS_TOLERANCE:
            break
    return level


def normalise_voice(raw, out):
    """voice.wav at about -14 LUFS. It is stereo, the same on both sides, because ffmpeg's own mono to stereo step
    lowers the level by 3 dB. Kokoro's output drifts up by about 0.04 while it speaks (a swell under 20 Hz, a third of
    its energy): nobody hears it, but it uses up the limiter's room, so a high-pass at VOICE_HIGHPASS_HZ takes it out."""
    level = measure_loudness(raw)[0] + 3.0  # raw is mono: a stereo copy measures 3 dB higher
    return normalise(["-i", raw], out, level, f"highpass=f={VOICE_HIGHPASS_HZ},aresample={RATE},pan=stereo|c0=c0|c1=c0,")


def read_wav(path):
    """A 16-bit WAV as float samples, one column per channel."""
    import numpy as np
    with wave.open(str(path)) as w:
        channels, raw = w.getnchannels(), w.readframes(w.getnframes())
    return np.frombuffer(raw, np.int16).reshape(-1, channels).astype(np.float32) / 32768


# --- Music and sound effects ---

def duck_curve(voice, rate=RATE):
    """How far the music rises over its level under the voice, in dB, one value per 10 ms: 0 while the voice speaks,
    up to GAP_LIFT_DB in a pause of LIFT_GAP_S or more. Taken from the voice's own sound, so it works the same over any
    shot. The dip starts DUCK_LEAD_S before the speech comes back and is all the way down by then."""
    import numpy as np
    frame = int(0.01 * rate)
    count = -(-len(voice) // frame)
    padded = np.zeros(count * frame, dtype=np.float32)
    padded[:len(voice)] = voice
    rms = np.sqrt((padded.reshape(-1, frame) ** 2).mean(1))
    speech = rms >= rms.max() * 10 ** (SPEECH_DB / 20) if rms.max() > 0 else np.zeros(count, bool)
    edges = np.flatnonzero(np.diff(np.concatenate(([1], speech.astype(int), [1]))))
    for a, b in zip(edges[::2], edges[1::2]):  # each pause, from a to b
        if b - a < LIFT_GAP_S / 0.01:
            speech[a:b] = True
    near = np.concatenate(([0], np.cumsum(speech)))
    lead, hold = round(DUCK_LEAD_S / 0.01), round(DUCK_HOLD_S / 0.01)
    i = np.arange(count)
    ducked = near[np.minimum(i + lead + 1, count)] - near[np.maximum(i - hold, 0)] > 0
    target = np.where(ducked, 0.0, GAP_LIFT_DB)
    up, down = GAP_LIFT_DB * 0.01 / DUCK_UP_S, GAP_LIFT_DB * 0.01 / DUCK_DOWN_S
    curve = np.empty(count)
    level = target[0]
    for n, want in enumerate(target):
        level = min(want, level + up) if want > level else max(want, level - down)
        curve[n] = level
    return curve


def fade(n, seconds, rate=RATE):
    """A smooth rise from 0 to 1 over the first samples of n."""
    import numpy as np
    ramp = np.ones(n)
    k = min(int(seconds * rate), n)
    ramp[:k] = np.sin(np.linspace(0, np.pi / 2, k)) ** 2
    return ramp


def music_bed(music, voice, volume, work):
    """The music under this voice: looped or cut to its length, a small dip where consonants sit, 16 LU under the
    voice while it speaks, up a little in its pauses, faded in and out. Returns the samples and what was done."""
    import numpy as np
    raw = work / "music.f32"
    seconds = len(voice) / RATE
    ffmpeg(["-stream_loop", "-1", "-i", music, "-t", f"{seconds:.3f}", "-vn", "-af", f"aresample={RATE},{MUSIC_PRESENCE}",
            *F32, raw])
    level = measure_loudness(raw, F32)[0]
    track = np.zeros((len(voice), 2), dtype=np.float32)
    decoded = np.fromfile(raw, np.float32).reshape(-1, 2)[:len(voice)]
    track[:len(decoded)] = decoded
    gain_db = (MUSIC_LUFS - level if math.isfinite(level) else 0.0) + 20 * math.log10(volume)
    curve = duck_curve(voice[:, 0])
    lift = np.interp(np.arange(len(voice)) / RATE, (np.arange(len(curve)) + 0.5) * 0.01, curve)
    gain = 10 ** ((gain_db + lift) / 20) * fade(len(voice), MUSIC_FADE_IN_S) * fade(len(voice), MUSIC_FADE_OUT_S)[::-1]
    lifted = float((curve > GAP_LIFT_DB / 2).sum() * 0.01)
    return track * gain[:, None].astype(np.float32), {"music_lufs": round(level, 1), "gain_db": round(gain_db, 2),
                                                      "lifted_s": round(lifted, 2)}


def lowpass(x, hz, rate=RATE):
    """Everything above hz gone, fading out over half an octave so nothing rings."""
    import numpy as np
    f = np.fft.rfftfreq(len(x), 1 / rate)
    keep = np.clip(1 - np.log2(np.maximum(f, 1) / hz) * 2, 0, 1)
    return np.fft.irfft(np.fft.rfft(x) * keep, len(x))


def swept_noise(rng, seconds, centre, octaves, rate=RATE):
    """Noise through a band-pass whose middle moves: centre(t) gives it in Hz, octaves is its width. Done frame by frame
    in the frequency domain."""
    import numpy as np
    size, hop = 1024, 256
    n = int(seconds * rate)
    noise = rng.standard_normal(n + size)
    window = np.hanning(size)
    freqs = np.maximum(np.fft.rfftfreq(size, 1 / rate), 1)
    out, weight = np.zeros(n + size), np.zeros(n + size)
    for start in range(0, n, hop):
        band = np.exp(-0.5 * (np.log2(freqs / centre((start + size / 2) / rate)) / octaves) ** 2)
        out[start:start + size] += np.fft.irfft(np.fft.rfft(noise[start:start + size] * window) * band, size) * window
        weight[start:start + size] += window ** 2
    return out[:n] / np.maximum(weight[:n], 0.1)


def whoosh(seed):
    """A soft whoosh: noise whose pitch climbs to the cut and falls away after it, quiet at the edges."""
    import numpy as np
    t = np.arange(int(WHOOSH_S * RATE)) / RATE
    rise, fall = WHOOSH_PEAK_S, WHOOSH_S - WHOOSH_PEAK_S
    centre = lambda s: 350 * (2400 / 350) ** (s / rise) if s < rise else 2400 * (800 / 2400) ** ((s - rise) / fall)
    sound = swept_noise(np.random.default_rng(seed), WHOOSH_S, centre, 0.55)
    shape = np.where(t < rise, (t / rise) ** 2, np.exp(-(t - rise) / 0.09)) * fade(len(t), 0.02)[::-1]
    return finish(sound * shape, WHOOSH_DB)


def hit(seed):
    """A punchy hit: a falling thump, some body at 150-400 Hz that a phone's speaker can play, and a short knock at
    1-4 kHz for the attack, lightly saturated so it carries."""
    import numpy as np
    t = np.arange(int(HIT_S * RATE)) / RATE
    thump = np.sin(2 * np.pi * np.cumsum(50 + 130 * np.exp(-t / 0.03)) / RATE) * np.exp(-t / 0.15)
    body = np.sin(2 * np.pi * 260 * t) * np.exp(-t / 0.04)
    knock = swept_noise(np.random.default_rng(seed), HIT_S, lambda s: 2200, 0.8) * np.exp(-t / 0.008)
    knock /= max(np.abs(knock).max(), 1e-9)
    sound = np.tanh(1.8 * (thump + 0.7 * body + 0.45 * knock)) * fade(len(t), 0.03)[::-1]
    return finish(sound, HIT_DB)


def finish(sound, peak_db):
    """Cut above SFX_LOWPASS_HZ, then set the loudest sample to peak_db."""
    import numpy as np
    sound = lowpass(sound, SFX_LOWPASS_HZ)
    return (sound / max(np.abs(sound).max(), 1e-9) * 10 ** (peak_db / 20)).astype(np.float32)


def plan_sfx(slots):
    """When each sound effect plays and why: a whoosh when a line starts or a pan begins, a hit when a card appears.
    A hit wins over a whoosh at the same moment. Nothing on the first frame, which is also where the loop starts again,
    and nothing in the loop ending."""
    events, started = [], set()
    for slot in slots:
        if slot.get("loop_tail"):
            continue
        at, visual, line = slot["start"] / FPS, slot["visual"], slot["line"] + 1
        if visual["kind"] == "card":
            events.append({"kind": "hit", "at_s": at, "why": f"line {line}: {visual['source']} appears"})
        elif slot["line"] not in started:
            events.append({"kind": "whoosh", "at_s": at, "why": f"line {line} starts"})
        elif visual.get("pan"):
            events.append({"kind": "whoosh", "at_s": at, "why": f"line {line}: a pan begins"})
        started.add(slot["line"])
    events = [e for e in events if e["at_s"] >= SFX_EARLIEST_S]
    kept = []
    for e in events:  # in time order already: a whoosh too near a hit, or near another whoosh, goes
        near = [k for k in kept if e["at_s"] - k["at_s"] < SFX_SPACING_S]
        if near and e["kind"] == "whoosh":
            continue
        if near and near[-1]["kind"] == "whoosh":
            kept.remove(near[-1])
        kept.append(e)
    return [{**e, "at_s": round(e["at_s"], 3)} for e in kept]


def sfx_track(events, n):
    """The sound effects as one mono track n samples long. A whoosh's loudest moment and a hit's start land on their
    moment."""
    import numpy as np
    track = np.zeros(n + int(max(WHOOSH_S, HIT_S) * RATE), dtype=np.float32)
    for i, e in enumerate(events):
        sound = hit(i) if e["kind"] == "hit" else whoosh(i)
        start = round(e["at_s"] * RATE) - (round(WHOOSH_PEAK_S * RATE) if e["kind"] == "whoosh" else 0)
        cut = max(0, -start)
        track[start + cut:start + len(sound)] += sound[cut:]
    return track[:n]


def mix_audio(voice_wav, music, volume, events, work):
    """The voice, the music bed and the sound effects in one WAV at -14 LUFS. Returns its path and what went in."""
    import numpy as np
    voice = read_wav(voice_wav)
    mix, info = voice.copy(), {}
    if music:
        bed, info = music_bed(music, voice, volume, work)
        mix += bed
    if events:
        mix += sfx_track(events, len(voice))[:, None]
    mixed = work / "mix.f32"
    mix.astype(np.float32).tofile(mixed)
    out = work / "mix.wav"
    info["mix_lufs"] = normalise([*F32, "-i", mixed], out, measure_loudness(mixed, F32)[0])
    return out, info


# --- Captions ---

def apply_tags(words, tags):
    """Mark the words of each tagged phrase with its colour, and with an id that keeps the phrase in one chunk."""
    keys = [w["key"] for w in words]
    for colour, phrases in tags.items():
        for phrase in phrases:
            for i in find_phrase(keys, phrase):
                for w in words[i:i + len(phrase.split())]:
                    w["colour"], w["tag"] = colour, (colour, phrase, i)


def caption_groups(words):
    """The words in chunks of 2-4, breaking at sentence ends and pauses, and never inside a tagged phrase."""
    groups, current, i = [], [], 0
    while i < len(words):
        atom = [words[i]]
        i += 1
        while atom[0]["tag"] is not None and i < len(words) and words[i]["tag"] == atom[0]["tag"]:
            atom.append(words[i])
            i += 1
        chars = len(" ".join(w["text"] for w in current + atom))
        if current and (len(current) + len(atom) > MAX_WORDS or chars > GROUP_CHARS
                        or current[-1]["sentence"] != atom[0]["sentence"]):
            groups.append(current)
            current = []
        current += atom
        if current[-1]["pause"] and len(current) >= MIN_WORDS:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    for a, b in zip(groups, groups[1:]):  # no lone last word if the chunk before can spare one
        if (len(b) == 1 and len(a) > MIN_WORDS and a[-1]["sentence"] == b[0]["sentence"]
                and a[-1]["tag"] is None and not a[-1]["pause"]):
            b.insert(0, a.pop())
    return groups


def text_width(text):
    """A line's width in average capitals of Anton: good enough to fit it, and explainertest.py measures the real pixels."""
    return sum(1.7 if c in "MW" else 0.55 if c in "IJ1.,:;'! " else 1.0 for c in text)


def fit_lines(texts, max_chars, size, keep_together=()):
    """Where to break the words onto a second line (None: no need), and a size that keeps the widest line on screen.
    keep_together holds the indexes of words that shouldn't start the second line."""
    break_at = None
    if text_width(" ".join(texts)) > max_chars and len(texts) > 1:
        places = [i for i in range(1, len(texts)) if i not in keep_together] or range(1, len(texts))
        break_at = min(places, key=lambda i: max(text_width(" ".join(texts[:i])), text_width(" ".join(texts[i:]))))
    pieces = [texts] if break_at is None else [texts[:break_at], texts[break_at:]]
    widest = max(text_width(" ".join(p)) for p in pieces)
    return break_at, size if widest <= max_chars else int(size * max_chars / widest)


def caption_text(words):
    """A chunk's text. Each untagged word turns HIGHLIGHT while it is being said and back to white when the next word
    starts; the last stays lit until the chunk goes. It is one event with timed colour changes (\\t, in ms from the
    event's start as ass_time rounds it), so the chunk pops in once and the highlight never jumps."""
    inside_a_tag = {i for i in range(1, len(words)) if words[i]["tag"] is not None and words[i]["tag"] == words[i - 1]["tag"]}
    break_at, size = fit_lines([w["text"] for w in words], CAPTION_CHARS, CAPTION_SIZE, inside_a_tag)
    shown_from = round(words[0]["start"] * 100) / 100
    at = lambda seconds: max(0, round((seconds - shown_from) * 1000))
    parts = []
    for i, w in enumerate(words):
        if i:
            parts.append("\\N" if i == break_at else " ")
        text = escape_ass(w["text"])
        if w["colour"]:
            parts.append(f"{{\\c{COLOURS[w['colour']]}}}{text}{{\\c{WHITE}}}")
            continue
        on = at(w["start"])
        tags = f"\\c{HIGHLIGHT}" if on == 0 else f"\\t({on},{on + 1},\\c{HIGHLIGHT})"
        if i + 1 < len(words):
            off = max(at(words[i + 1]["start"]), on + 1)
            tags += f"\\t({off},{off + 1},\\c{WHITE})"
        parts.append(f"{{{tags}}}{text}{{\\c{WHITE}}}")
    return f"{{\\fs{size}{POP}}}" + "".join(parts)


def title_layout(text):
    """A title's lines as (text, style) pieces, and the size that fits the widest. Plain text breaks in two by itself
    when it's too wide; marked text keeps the lines it was given."""
    lines = card_lines(text)
    if len(lines) == 1 and all(style is None for _, style in lines[0]):
        words = text.split()
        break_at, size = fit_lines(words, TITLE_CHARS, TITLE_SIZE)
        pieces = [words] if break_at is None else [words[:break_at], words[break_at:]]
        return [[(" ".join(p), None)] for p in pieces], size
    tidy = []
    for line in lines:
        line = [(t, s) for t, s in line]
        line[0] = (line[0][0].lstrip(), line[0][1])
        line[-1] = (line[-1][0].rstrip(), line[-1][1])
        tidy.append([(t, s) for t, s in line if t])
    widest = max(text_width("".join(t for t, _ in line)) for line in tidy)
    return tidy, TITLE_SIZE if widest <= TITLE_CHARS else int(TITLE_SIZE * TITLE_CHARS / widest)


def title_text(text, y, pop):
    lines, size = title_layout(text)
    paint = lambda t, s: f"{{\\c{COLOURS['green']}}}{escape_ass(t)}{{\\c{WHITE}}}" if s == "green" else escape_ass(t)
    body = "\\N".join("".join(paint(t, s) for t, s in line) for line in lines)
    return f"{{\\an5\\pos({CENTRE_X},{y})\\fs{size}{pop}}}" + body


def ink_width(text, size):
    """About how wide Anton draws the text at this size, in pixels: within a few percent, a little over."""
    return size * sum(GLYPH_WIDTHS.get(c, 0.28) for c in text)


def strike_bars(text, y):
    """The drawing for each crossed-out piece of a title, placed from Anton's measured sizes: the piece's capitals
    are covered with some to spare on each side, and the bar never leaves the safe area. Each grows from its left end
    once the old text has been seen."""
    lines, size = title_layout(text)
    bars = []
    for i, line in enumerate(lines):
        plain = "".join(t for t, _ in line)
        left_edge, done = CENTRE_X - ink_width(plain, size) / 2, ""
        middle = y + (i - (len(lines) - 1) / 2) * LINE_STEP * size + CAP_MIDDLE * size
        for t, style in line:
            if style == "strike":
                lead = t[:len(t) - len(t.lstrip())]
                left = max(left_edge + ink_width(done + lead, size) - BAR_PAD * size, SAFE_LEFT)
                right = min(left_edge + ink_width(done + t.rstrip(), size) + BAR_PAD * size, W - SAFE_RIGHT)
                width, height = right - left, BAR_HEIGHT * size
                bars.append(f"{{\\an4\\pos({left:.0f},{middle:.0f})\\org({left + width / 2:.0f},{middle:.0f})"
                            f"\\frz{BAR_TILT}\\bord{BAR_OUTLINE}\\shad0"
                            f"\\c{COLOURS['red']}\\3c&H000000&\\fscx0\\t({BAR_DELAY_MS},{BAR_DELAY_MS + BAR_GROW_MS},"
                            f"\\fscx100)\\p1}}m 0 0 l {width:.0f} 0 {width:.0f} {height:.0f} 0 {height:.0f}")
            done += t
    return bars


def title_events(layer, start, end, text, y, pop):
    """A title and the bars that cross out its ~marked~ pieces. They share its layer and come after it, so libass draws
    them on top; everything here has a \\pos, so nothing gets pushed aside."""
    fade = "\\fad(0,120)" if "\\fad" in pop else ""
    events = [dialogue(layer, start, end, "Title", title_text(text, y, pop))]
    return events + [dialogue(layer, start, end, "Title", bar.replace("\\p1}", fade + "\\p1}", 1))
                     for bar in strike_bars(text, y)]


def dialogue(layer, start, end, style, text):
    return f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}"


def ass_document(events):
    fields = ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
              "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
              "MarginR, MarginV, Encoding")

    def style(name, size, outline, alignment, margin_v, shadow=0):
        back = f"&H{SHADOW_ALPHA}000000" if shadow else "&H00000000"
        return (f"Style: {name},{FONT},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,{back},0,0,0,0,100,100,0,0,1,{outline},"
                f"{shadow},{alignment},{SAFE_LEFT},{SAFE_RIGHT},{margin_v},1")
    return "\n".join(["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2",
                      "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "", "[V4+ Styles]", fields,
                      style("Caption", CAPTION_SIZE, CAPTION_OUTLINE, 2, H - CAPTION_BOTTOM, CAPTION_SHADOW),
                      style("Title", TITLE_SIZE, TITLE_OUTLINE, 5, 0), "", "[Events]",
                      "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text", *events, ""])


def caption_events(words, total_s):
    """Each chunk shows from its first word until the next chunk, or a moment after its last word if a pause follows."""
    events, groups = [], caption_groups(words)
    for group, following in zip(groups, groups[1:] + [None]):
        end = total_s
        if following:
            end = min(following[0]["start"], group[-1]["end"] + CAPTION_LINGER_S)
        events.append(dialogue(2, group[0]["start"], end, "Caption", caption_text(group)))
    return events


def card_events(slots, open_card):
    """Cards on layer 0, the open card on 1, both under the captions on 2."""
    events = title_events(1, 0, OPEN_CARD_S, open_card, OPEN_CARD_Y, OPEN_POP) if open_card else []
    for slot in slots:
        if slot["visual"]["kind"] == "card":
            events += title_events(0, slot["start"] / FPS, slot["end"] / FPS, slot["visual"]["text"], CARD_Y, POP)
    return events


# --- The timeline ---

def plan_slots(lines, line_starts, total_frames):
    """Every visual's frames, in order. A line's time is shared by its visuals' weights (1 each unless set). Cuts come
    from absolute times, so rounding never adds up to a drift. The loop ending takes the last frames of the voice's own
    time: adding them after it would make the video longer than the voice."""
    slots = []
    for n, line in enumerate(lines):
        a = round(line_starts[n] * FPS)
        b = round(line_starts[n + 1] * FPS) if n + 1 < len(lines) else total_frames
        weights = [v.get("weight", 1.0) for v in line["visuals"]]
        edges = [0.0]
        for w in weights:
            edges.append(edges[-1] + w)
        cut = lambda share: a + math.floor((b - a) * share / edges[-1] + 1e-9)
        for i, visual in enumerate(line["visuals"]):
            start, end = cut(edges[i]), cut(edges[i + 1])
            if end > start:
                slots.append({"line": n, "start": start, "end": end, "visual": visual})
    tail = total_frames - LOOP_FRAMES
    kept = [s for s in slots if s["start"] < tail]
    if kept:
        kept[-1]["end"] = min(kept[-1]["end"], tail)
        if kept[-1]["end"] - kept[-1]["start"] < MIN_SLOT_FRAMES and len(kept) > 1:
            tail = kept.pop()["start"]
        kept.append({"line": len(lines) - 1, "start": tail, "end": total_frames, "visual": slots[0]["visual"],
                     "loop_tail": True})
        slots = kept
    for slot in slots:
        slot["frames"] = slot["end"] - slot["start"]
    return slots


def motion_kinds(rng):
    """All four motions in shuffled rounds, without the same one twice running across a round's edge."""
    last = None
    while True:
        round_ = MOTIONS[:]
        rng.shuffle(round_)
        if round_[0] == last:
            round_.reverse()
        yield from round_
        last = round_[-1]


def crop_box(visual):
    """The 9:16 part of an image that fills the screen, centred on its focus as far as the picture allows: x, y, width,
    height, and where the subject sits across and down that part (0-1)."""
    iw, ih = visual["size"]
    fx, fy = visual["focus"], visual.get("focus_y", 0.5)
    if iw * H > ih * W:
        cw, ch = round(ih * W / H), ih
    else:
        cw, ch = iw, round(iw * H / W)
    x = min(max(round(fx * iw - cw / 2), 0), iw - cw)
    y = min(max(round(fy * ih - ch / 2), 0), ih - ch)
    return x, y, cw, ch, (fx * iw - x) / cw, (fy * ih - y) / ch


def make_motion(kind, speed, frames, subject=0.5, middle=0.5):
    """A move as the window's zoom and where its middle is across the crop (0 left, 1 right), at start and end, and how
    far down (middle, which stays put). A zoom keeps the subject in the middle as far as the crop allows. A drift
    holds a slight zoom, which gives it room on any picture, even one with no width to spare, and slides from one side
    of that room to the other."""
    seconds = max(frames - 1, 1) / FPS
    if kind.startswith("zoom"):
        amount = min(ZOOM_PER_S * speed * seconds, ZOOM_MAX)
        zoom = (1.0, 1.0 + amount) if kind == "zoom_in" else (1.0 + amount, 1.0)
        return {"kind": kind, "speed": round(speed, 3), "zoom": zoom, "centre": (subject, subject), "middle": middle}
    travel = min(PAN_PX_PER_S * speed * seconds, PAN_MAX_PX)
    fixed = W / (W - travel)
    half = 0.5 / fixed
    return {"kind": kind, "speed": round(speed, 3), "zoom": (fixed, fixed),
            "centre": (1.0 - half, half) if kind == "pan_left" else (half, 1.0 - half), "middle": middle}


def make_reveal(visual, direction):
    """A pan across the whole width of a wide picture, at full height, from one edge to the other at a steady speed.
    centre is across the whole picture here, not the crop."""
    share = crop_box(visual)[2] / visual["size"][0]
    a, b = PAN_DIRECTIONS[direction]
    edge = lambda f: share / 2 + f * (1 - share)
    return {"kind": f"reveal_{direction}", "speed": 1.0, "zoom": (1.0, 1.0), "centre": (edge(a), edge(b)), "wide": True}


def return_motion(opening, opening_frames, frames):
    """The opening's first frames played backwards, so the Short's last frame is exactly its first."""
    p = min((frames - 1) / max(opening_frames - 1, 1), 1.0)
    at = lambda pair: pair[0] + (pair[1] - pair[0]) * p
    motion = {"kind": "back_to_first_frame", "speed": opening["speed"],
              "zoom": (at(opening["zoom"]), opening["zoom"][0]), "centre": (at(opening["centre"]), opening["centre"][0]),
              "middle": opening.get("middle", 0.5)}
    if opening.get("wide"):
        motion["wide"] = True
    return motion


def assign_motions(slots, seed):
    """A different move on each image, seeded so a re-render of the same title looks the same. A shot with a pan gets
    its reveal. A subject well off the crop's middle (a focus near the picture's edge) gets a zoom, never a drift, which
    could slide it out of the frame."""
    rng = random.Random(seed)
    kinds = motion_kinds(rng)
    last = None
    for slot in slots:
        visual = slot["visual"]
        if visual["kind"] != "image" or slot.get("loop_tail"):
            continue
        if visual.get("pan"):
            slot["motion"] = make_reveal(visual, visual["pan"])
            continue
        kind = next(kinds)
        if kind == last:  # only possible after a swap below
            kind = next(kinds)
        speed = rng.uniform(*SPEED_RANGE)
        subject, middle = crop_box(visual)[4:]
        if kind.startswith("pan") and abs(subject - 0.5) > OFF_CENTRE:
            kind = "zoom_out" if last == "zoom_in" else "zoom_in"
        slot["motion"] = make_motion(kind, speed, slot["frames"], subject, middle)
        last = kind
    if slots[-1].get("loop_tail") and slots[0]["visual"]["kind"] == "image":
        slots[-1]["motion"] = return_motion(slots[0]["motion"], slots[0]["frames"], slots[-1]["frames"])


def pan_speed(slot):
    """How fast a reveal moves, in screen widths a second."""
    visual, motion = slot["visual"], slot["motion"]
    widths = abs(motion["centre"][1] - motion["centre"][0]) * visual["size"][0] / crop_box(visual)[2]
    return widths / (max(slot["frames"] - 1, 1) / FPS)


@functools.lru_cache(maxsize=None)
def seam_psnr(path, frames):
    """How alike a clip's last and first frames are, in dB."""
    p = str(path)
    err = ffmpeg(["-i", p, "-i", p, "-filter_complex",
                  f"[0:v]select='eq(n,{frames - 1})',setpts=PTS-STARTPTS,scale=270:-2[a];"
                  "[1:v]select='eq(n,0)',setpts=PTS-STARTPTS,scale=270:-2[b];[a][b]psnr",
                  "-frames:v", "1", "-f", "null", "-"], loglevel="info")
    m = re.search(r"average:([\d.]+|inf)", err)
    return float(m.group(1)) if m else 0.0


def fill_mode(visual, start, frames):
    """How a video fills its slot. A clip too short for the slot loops only if its seam really is invisible. Rockstar's
    character clips are not loops (last and first frame are 11-18 dB alike), and they end on a close-up, so they hold."""
    if (visual["length"] - start) * FPS >= frames:
        return "play"
    if start == 0 and seam_psnr(visual["path"], visual["frames"]) >= CLEAN_SEAM_DB:
        return "loop"
    return "hold"


def plan_videos(slots):
    """Each video slot's start time and fill. The loop ending runs up to the point the opening starts from, when the
    file has that much before it, so the join back to the start is a continuous shot."""
    for slot in slots:
        visual = slot["visual"]
        if visual["kind"] != "video":
            continue
        slot["start_s"] = visual["start"]
        if slot.get("loop_tail") and visual["start"] >= slot["frames"] / FPS:
            slot["start_s"] = visual["start"] - slot["frames"] / FPS
        slot["fill"] = fill_mode(visual, slot["start_s"], slot["frames"])


def slot_warnings(slots, lines, last_line_frames):
    warnings = []
    for slot in slots:
        seconds, visual = slot["frames"] / FPS, slot["visual"]
        panning = bool(visual.get("pan")) and not slot.get("loop_tail")
        if seconds > (LONG_PAN_S if panning else LONG_SLOT_S) and not slot.get("loop_tail"):
            warnings.append(f"line {slot['line'] + 1}: {visual['source']} stays up {seconds:.1f} s; the target is a new "
                            "picture every 1.5-2 s, so give this line another shot")
        if panning and pan_speed(slot) > FAST_PAN:
            warnings.append(f"line {slot['line'] + 1}: {visual['source']} crosses {pan_speed(slot):.1f} screen widths a "
                            f"second, faster than {FAST_PAN:g}; give it a bigger weight")
        if slot.get("fill") == "hold":
            held = seconds - (visual["length"] - slot["start_s"])
            if held > LONG_HOLD_S:
                warnings.append(f"line {slot['line'] + 1}: {visual['source']} runs out and freezes for {held:.1f} s")
    if last_line_frames < FPS:
        warnings.append(f"line {len(lines)}: its own visuals show for only {last_line_frames / FPS:.1f} s, because the "
                        "loop ending takes the last 1.5 s")
    return warnings


# --- Rendering ---

def image_filter(visual, motion, frames):
    """Crop to 9:16 around the focus, enlarge, then move a window over it. A reveal pans over the whole picture instead."""
    if motion.get("wide"):
        return reveal_filter(visual, motion, frames)
    x, y, crop_w, crop_h, _, _ = crop_box(visual)
    p = f"on/{max(frames - 1, 1)}"
    (z0, z1), (c0, c1), middle = motion["zoom"], motion["centre"], motion.get("middle", 0.5)
    return (f"crop={crop_w}:{crop_h}:{x}:{y},scale={W * CANVAS_SCALE}:{H * CANVAS_SCALE}:flags=lanczos:{COLOUR_OUT},"
            f"format=yuv444p,zoompan=z='{z0:.5f}+({z1 - z0:.5f})*{p}':"
            f"x='clip(({c0:.5f}+({c1 - c0:.5f})*{p})*iw-iw/zoom/2,0,iw-iw/zoom)':"
            f"y='clip({middle:.5f}*ih-ih/zoom/2,0,ih-ih/zoom)':d={frames}:s={W}x{H}:fps={FPS},format=yuv420p,setsar=1")


def reveal_filter(visual, motion, frames):
    """A pan over the whole picture. zoompan can't do it: its window keeps the picture's own shape. So the picture is
    enlarged to 3x the screen's height once, that one frame repeats, a 9:16 window slides across it a whole number of
    enlarged pixels per frame (a third of a screen pixel, too fine to see as a shake), and each frame is scaled down.
    A pan moves tens of pixels a frame, so a sideways blur the size of a camera's half-open shutter keeps it smooth."""
    iw, ih = visual["size"]
    tall = H * CANVAS_SCALE
    wide = round(iw * tall / ih / 2) * 2
    window = W * CANVAS_SCALE
    (c0, c1), steps = motion["centre"], max(frames - 1, 1)
    x = f"clip(({c0:.6f}+({c1 - c0:.6f})*n/{steps})*{wide}-{window // 2},0,{wide - window})"
    blur = PAN_BLUR * abs(c1 - c0) * wide / steps / CANVAS_SCALE
    blur = f",gblur=sigma={blur:.2f}:sigmaV=0:steps=2" if blur >= 0.3 else ""
    return (f"scale={wide}:{tall}:flags=lanczos:{COLOUR_OUT},format=yuv444p,loop=loop=-1:size=1,setpts=N/{FPS}/TB,"
            f"crop={window}:{tall}:x='{x}':y=0,scale={W}:{H}:flags=lanczos{blur},format=yuv420p,setsar=1")


def video_filter(focus=0.5, focus_y=0.5):
    """Fill the frame, cropping around the focus, never a blurred background. tpad holds the last frame if the file
    runs out."""
    return (f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos:{COLOUR_OUT},"
            f"crop={W}:{H}:x='clip({focus:.4f}*iw-{W // 2},0,iw-{W})':y='clip({focus_y:.4f}*ih-{H // 2},0,ih-{H})',"
            "setsar=1,format=yuv420p,tpad=stop=-1:stop_mode=clone")


def pick_encoder():
    """h264_nvenc if a real test encode works, else libx264: settings for the pieces, then for the final file. The pieces
    have no B-frames, so joining them can't drop or reorder a frame."""
    test = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
                           "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True)
    if test.returncode == 0:
        return ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p4", "-tune", "hq", "-rc", "constqp", "-qp", "16",
                               "-bf", "0", "-g", "30"],
                ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "20", "-b:v", "0",
                 "-profile:v", "high"])
    return ("libx264", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-bf", "0", "-g", "30"],
            ["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-profile:v", "high"])


def render_segment(slot, out, encode):
    """One visual as its exact number of frames. -an: a source's own audio never comes along."""
    visual, frames = slot["visual"], slot["frames"]
    if visual["kind"] == "card":
        inputs, picture = ["-f", "lavfi", "-i", f"color=c={CARD_COLOUR}:s={W}x{H}:r={FPS}"], "setsar=1,format=yuv420p"
    elif visual["kind"] == "image":
        rate = ["-framerate", FPS] if slot["motion"].get("wide") else []  # the repeated frame's rate is the input's
        inputs = ["-f", "image2", "-pattern_type", "none", *rate, "-i", visual["path"]]
        picture = image_filter(visual, slot["motion"], frames)
    else:
        inputs = ["-stream_loop", "-1"] if slot["fill"] == "loop" else []
        inputs += ["-ss", f"{slot['start_s']:.3f}", "-i", visual["path"]]
        picture = video_filter(visual.get("focus", 0.5), visual.get("focus_y", 0.5))
    ffmpeg([*inputs, "-an", "-sn", "-dn", "-vf", picture, "-frames:v", frames, *encode, *COLOUR_TAGS, out])


def font_warning(work):
    """A warning if libass ended up with another font: it would swap one in without saying so."""
    (work / "check.ass").write_text(ass_document([dialogue(0, 0, 1, "Caption", "TEST")]), encoding="utf-8")
    err = ffmpeg(["-f", "lavfi", "-i", "color=black:s=64x64:d=0.1", "-frames:v", "1", "-vf", "ass=check.ass:fontsdir=.",
                  "-f", "null", "-"], cwd=work, loglevel="debug")
    m = re.search(r"fontselect: \(.*?\) -> ([^,\r\n]+)", err)
    return None if m and FONT in m.group(1) else f"libass didn't confirm {FONT}: {m.group(1).strip() if m else 'no font line'}"


def render(script_path, root, out_root):
    started = time.perf_counter()
    if not FFMPEG or not FFPROBE:
        raise ScriptError("ffmpeg and ffprobe must be on PATH")
    script = load_script(script_path, root)
    kokoro = load_kokoro()
    warnings = script_warnings(script)
    run_dir = Path(out_root) / script["slug"] / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    timing = {}

    t = time.perf_counter()
    audio, rate, words, line_starts, splits = synthesize(kokoro, script["lines"], script["voice"], script["speed"])
    timing["voice"] = round(time.perf_counter() - t, 1)
    quiet = longest_quiet(audio, rate)
    method = "model phoneme durations"
    if splits:
        method += f"; {splits} sentence(s) fell back to splitting by word length"
        warnings.append(f"{splits} sentence(s) couldn't be lined up with the model's timing and were split by word length")

    with tempfile.TemporaryDirectory(prefix="work_", dir=run_dir) as work_name:
        work = Path(work_name)
        save_wav(work / "raw.wav", audio, rate)
        voice_lufs = normalise_voice(work / "raw.wav", run_dir / "voice.wav")
        voice_s = float(probe(run_dir / "voice.wav")["format"]["duration"])
        total_frames = round(voice_s * FPS)
        total_s = total_frames / FPS

        slots = plan_slots(script["lines"], line_starts, total_frames)
        assign_motions(slots, script["slug"])
        plan_videos(slots)
        last_line = sum(s["frames"] for s in slots if s["line"] == len(script["lines"]) - 1 and not s.get("loop_tail"))
        warnings += slot_warnings(slots, script["lines"], last_line)
        warnings += runtime_warnings(voice_s)

        events = card_events(slots, script["open_card"]) + caption_events(words, total_s)
        (run_dir / "captions.ass").write_text(ass_document(events), encoding="utf-8")
        shutil.copy(FONT_FILE, work)
        font_problem = font_warning(work)
        if font_problem:
            warnings.append(font_problem)

        t = time.perf_counter()
        music, music_note = pick_music(script["music"], root, voice_s, script["slug"])
        if script["music"] == "auto" and music is None:
            warnings.append(f'music is "auto" but {root / MUSIC_DIR} has no audio files, so this Short has no music')
        sfx = plan_sfx(slots) if script["sfx"] else []
        mix, sound = mix_audio(run_dir / "voice.wav", music, script["music_volume"], sfx, work)
        timing["sound"] = round(time.perf_counter() - t, 1)

        encoder, piece_args, final_args = pick_encoder()
        t = time.perf_counter()
        names = [f"seg_{i:04d}.mp4" for i in range(len(slots))]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            jobs = [pool.submit(render_segment, slot, work / name, piece_args) for slot, name in zip(slots, names)]
            for job in jobs:
                job.result()
        timing["segments"] = round(time.perf_counter() - t, 1)
        (work / "segments.txt").write_text("".join(f"file '{n}'\n" for n in names), encoding="utf-8")

        t = time.perf_counter()
        inputs = ["-f", "concat", "-safe", "0", "-i", "segments.txt", "-i", mix]
        graph = "[0:v]ass=../captions.ass:fontsdir=.,format=yuv420p[p]"
        ffmpeg([*inputs, "-filter_complex", graph, "-map", "[p]", "-map", "1:a", "-frames:v", total_frames,
                "-r", FPS, *final_args, *COLOUR_TAGS, "-c:a", "aac", "-b:a", "192k", "-ar", RATE, "-ac", "2",
                "-movflags", "+faststart", run_dir / "short.mp4"], cwd=work)
        timing["final"] = round(time.perf_counter() - t, 1)

    final = probe(run_dir / "short.mp4")
    final_lufs, final_peak = measure_loudness(run_dir / "short.mp4")
    if abs(final_lufs - TARGET_LUFS) > 1:
        warnings.append(f"the final loudness is {final_lufs:.1f} LUFS; the target is {TARGET_LUFS:.0f}")
    video_s = float(final["format"]["duration"])
    timing["total"] = round(time.perf_counter() - started, 1)

    def at_second(second):
        frame = min(second * FPS, total_frames - 1)
        return next(s["visual"]["source"] for s in slots if s["start"] <= frame < s["end"])
    shots = shot_stats(slots)
    log = {"script": str(script_path), "title": script["title"], "voice": script["voice"], "speed": script["speed"],
           "encoder": encoder, "timing_method": method,
           "durations": {"voice_s": round(voice_s, 3), "video_s": round(video_s, 3), "frames": total_frames,
                         "target_s": [MIN_TOTAL_S, MAX_TOTAL_S],
                         "lines": [{"line": n + 1, "start_s": round(s, 3)} for n, s in enumerate(line_starts)]},
           "shots": shots,
           "loudness": {"voice_lufs": voice_lufs, "mix_lufs": sound["mix_lufs"], "final_lufs": final_lufs,
                        "final_true_peak_db": final_peak},
           "music": {"file": str(music) if music else None, "chosen": music_note, "volume": script["music_volume"],
                     "track_lufs": sound.get("music_lufs"), "gain_db": sound.get("gain_db"),
                     "under_voice_lu": round(TARGET_LUFS - MUSIC_LUFS - 20 * math.log10(script["music_volume"]), 1)
                     if music else None,
                     "lift_in_pauses_db": GAP_LIFT_DB if music else None, "lifted_s": sound.get("lifted_s")},
           "sfx": sfx, "captions": "word by word: the word being said turns yellow; tagged words keep their colour",
           "longest_quiet_s": round(quiet, 2), "words": len(words),
           "word_times": [{"text": w["text"], "start_s": round(w["start"], 3), "end_s": round(w["end"], 3)} for w in words],
           "visuals": [{"line": s["line"] + 1, "source": s["visual"]["source"], "start_s": round(s["start"] / FPS, 3),
                        "seconds": round(s["frames"] / FPS, 3), "kind": s["visual"]["kind"],
                        "focus": [s["visual"]["focus"], s["visual"]["focus_y"]] if "focus" in s["visual"] else None,
                        "pan": s["visual"].get("pan"),
                        "weight": s["visual"].get("weight", 1.0),
                        "motion": s["motion"]["kind"] if "motion" in s else None,
                        "motion_speed": s["motion"]["speed"] if "motion" in s else None,
                        "pan_widths_per_s": round(pan_speed(s), 2) if s.get("motion", {}).get("wide") else None,
                        "fill": s.get("fill"), "loop_tail": bool(s.get("loop_tail"))} for s in slots],
           "per_second": [{"second": sec, "visual": at_second(sec)} for sec in range(math.ceil(total_s))],
           "warnings": warnings, "render_s": timing}
    (run_dir / "log.json").write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Run folder: {run_dir}")
    print(f"  Runtime {video_s:.1f} s ({runtime_note(video_s)})")
    print(f"  Shots: {shots['count']} ({shots['own']} plus the {LOOP_FRAMES / FPS:.1f} s loop ending), "
          f"average {shots['average_s']:.2f} s, longest {shots['longest_s']:.2f} s")
    if music:
        print(f"  Music: {music.name} ({music_note}), {log['music']['under_voice_lu']:g} LU under the voice, "
              f"up {GAP_LIFT_DB:g} dB in pauses of {LIFT_GAP_S:g} s or more")
    else:
        print("  Music: none")
    kinds = [e["kind"] for e in sfx]
    print(f"  Sound effects: {kinds.count('whoosh')} whooshes, {kinds.count('hit')} hits" if script["sfx"] else
          "  Sound effects: off")
    print(f"  Loudness: {final_lufs:.1f} LUFS, true peak {final_peak:.1f} dB")
    print(f"  Voice {script['voice']} at speed {script['speed']:g}, made in {timing['total']:.0f} s with {encoder}")
    for warning in warnings:
        print(f"  WARNING: {warning}")
    return run_dir


def shot_stats(slots):
    """How many shots there are and how long they stay up. The average and longest leave out the loop ending, which is
    always 1.5 s and repeats the first shot."""
    own = [s["frames"] / FPS for s in slots if not s.get("loop_tail")]
    return {"count": len(slots), "own": len(own), "average_s": round(sum(own) / len(own), 2) if own else 0.0,
            "longest_s": round(max(own), 2) if own else 0.0}


def runtime_note(seconds):
    if seconds > MAX_TOTAL_S:
        return f"over the {MIN_TOTAL_S}-{MAX_TOTAL_S} s target by {seconds - MAX_TOTAL_S:.1f} s"
    if seconds < MIN_TOTAL_S:
        return f"target {MIN_TOTAL_S}-{MAX_TOTAL_S} s: {MIN_TOTAL_S - seconds:.1f} s under, which is fine"
    return f"inside the {MIN_TOTAL_S}-{MAX_TOTAL_S} s target"


def runtime_warnings(seconds):
    """Over the target warns: a long Short loses people. Under it doesn't."""
    if seconds > MAX_TOTAL_S:
        return [f"the Short is {seconds:.1f} s, over the {MIN_TOTAL_S}-{MAX_TOTAL_S} s target: cut words rather than "
                f"speed the voice past {MAX_SPEED:g}"]
    return []


def main():
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Turn a script file into a finished explainer Short.")
    parser.add_argument("script")
    parser.add_argument("--root", type=Path, default=ROOT, help="the folder visuals and music are relative to")
    parser.add_argument("--out", type=Path, default=ROOT / "explainers", help="where the run folders go")
    args = parser.parse_args()
    try:
        render(args.script, args.root, args.out)
    except ScriptError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
