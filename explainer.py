"""Turn a script file into a finished 1080x1920 explainer Short: Kokoro voice, word-timed captions, fast cuts, loop ending.

A script is a JSON file (see examples/explainer_sample.json). Each line says something over one or more visuals, which
share that line's time evenly. A visual is a video ("rockstar/x.mp4@0:42"), an image ("rockstar/x.jpg", or
"rockstar/x.jpg@0.7" to frame it 70 % of the way across) or a text card ("card:SOME TEXT"). Everything is checked first,
so a missing file or a bad time stops before anything renders.

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
MIN_TOTAL_S, MAX_TOTAL_S = 55, 75    # outside this the log and the console warn
DEFAULT_VOICE, DEFAULT_SPEED = "am_puck", 1.18
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Voice. The model leaves 0.2-0.3 s of near silence after every sentence even with its own trim on, so the tail is cut
# here. -45 dB keeps a soft ending: at -40 dB the "th" of "myth" lost 80 ms.
TAIL_DB, TAIL_KEEP_S, TAIL_FADE_S, GAP_S = -45, 0.04, 0.02, 0.12
BURST_GAP, BURST_LONG, BURST_DB = 7, 10, -30  # 10 ms frames: this far from the speech, shorter and fainter than this: not speech
TARGET_LUFS, LUFS_TOLERANCE = -14.0, 0.3
LIMIT = 0.84                         # about -1.5 dB, the voice's ceiling
MUSIC_LUFS = -32.0                   # 18 LU under the voice, so the mix's loudness barely moves
NOT_A_SOUND = set(' ;:,.!?¡¿—…"«»“”()ˈˌ')  # in the phonemes, but nothing to align words on
MIN_MATCH = 0.6                      # below this the phonemes of the words and the sentence are too unlike to trust

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
POP = "\\fscx88\\fscy88\\t(0,70,\\fscx106\\fscy106)\\t(70,130,\\fscx100\\fscy100)"
OPEN_POP = POP + "\\fad(0,120)"
CARD_COLOUR = "0x12121C"

# Timeline.
LOOP_FRAMES = 45                     # the last 1.5 s shows the first visual again
MIN_SLOT_FRAMES = 12                 # a visual left with less than this before the loop ending is dropped
LONG_SLOT_S, LONG_HOLD_S = 3.5, 1.0  # warn: the target is about 2 s per visual, and a long freeze looks stuck
CLEAN_SEAM_DB = 30                   # a clip's last and first frame must be this alike to loop it; else it holds

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


def parse_visual(raw, root):
    """One visual as a dict, checked against the real file. ScriptError says what is wrong."""
    if not isinstance(raw, str) or not raw.strip():
        raise ScriptError(f"{raw!r} is not a visual")
    if raw.startswith("card:"):
        if not raw[5:].strip():
            raise ScriptError("a card with no text")
        return {"source": raw, "kind": "card", "text": raw[5:].strip().upper()}
    name, at = raw, None
    if "@" in raw and not (root / raw).exists():  # a file name may hold an @ of its own
        name, _, at = raw.rpartition("@")
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
    if ext in IMAGE_EXTS:
        focus = 0.5
        if at is not None:
            focus = float(at) if re.fullmatch(r"0(\.\d+)?|1(\.0+)?", at) else None
            if focus is None:
                raise ScriptError(f"{raw}: after an image's @ goes where to frame it, 0 (left edge) to 1 (right edge)")
        return {"source": raw, "kind": "image", "path": path, "focus": focus,
                "size": (int(picture["width"]), int(picture["height"]))}
    if "duration" not in info.get("format", {}):
        raise ScriptError(f"{path.name}: ffprobe can't read its length")
    length = float(info["format"]["duration"])
    start = 0.0
    if at is not None:
        start = parse_time(at)
        if start is None:
            raise ScriptError(f"{raw}: bad time {at!r}, use 42, 0:42 or 1:02:03")
    if start >= length:
        raise ScriptError(f"{raw}: starts at {start:.1f} s but the video is only {length:.1f} s long")
    frames = str(picture.get("nb_frames", ""))
    frames = int(frames) if frames.isdigit() else round(length * FPS)
    return {"source": raw, "kind": "video", "path": path, "start": start, "length": length, "frames": frames}


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
    problems = [f"unknown setting {k!r}" for k in script if k not in {"title", "voice", "speed", "open_card", "music", "lines"}]

    title = script.get("title")
    if not isinstance(title, str) or not re.search(r"[A-Za-z0-9]", title):
        problems.append("title is missing, or has no letters or digits for the folder name")
    voice = script.get("voice", DEFAULT_VOICE)
    if not isinstance(voice, str):
        problems.append("voice must be a voice name such as am_puck")
    speed = script.get("speed", DEFAULT_SPEED)
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0.5 <= speed <= 2.0:
        problems.append("speed must be a number from 0.5 to 2.0")
    open_card = script.get("open_card")
    if open_card is not None and (not isinstance(open_card, str) or not open_card.strip()):
        problems.append("open_card must be text, or null for none")
    music = script.get("music")
    music_path = None
    if music is not None:
        music_path = root / str(music)
        if not music_path.is_file():
            problems.append(f"music file not found: {music_path}")
        elif stream_of(probe(music_path), "audio") is None:
            problems.append(f"{music_path.name}: ffprobe finds no audio in it")

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
            "open_card": open_card.strip().upper() if open_card else None, "music": music_path, "lines": parsed}


def script_warnings(script):
    return [f"line {n}: espeak reads ${m} as \"dollar {m}\", so write \"{m} dollars\" instead"
            for n, line in enumerate(script["lines"], 1) for m in re.findall(r"\$(\d[\d,.]*\d|\d)", line["say"])]


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


def measure_loudness(path):
    """Integrated loudness (LUFS) and true peak (dB) of a file."""
    err = ffmpeg(["-i", path, "-vn", "-af", "loudnorm=print_format=json", "-f", "null", "-"], loglevel="info")
    m = json.loads(err[err.rindex("{"):err.rindex("}") + 1])
    return float(m["input_i"]), float(m["input_tp"])


def normalise_voice(raw, out):
    """voice.wav at about -14 LUFS, by gain and a limiter. Two-pass loudnorm falls back to its dynamic mode on this voice
    (the peaks leave no room) and lands near -15. It is stereo, the same on both sides, because ffmpeg's own mono to
    stereo step lowers the level by 3 dB. Each pass starts again from raw, so the limiter runs only once."""
    level = measure_loudness(raw)[0] + 3.0  # raw is mono: a stereo copy measures 3 dB higher
    gain = 0.0
    for _ in range(4):
        gain += TARGET_LUFS - level
        ffmpeg(["-i", raw, "-af", f"aresample=48000,pan=stereo|c0=c0|c1=c0,volume={gain:.2f}dB,"
                f"alimiter=limit={LIMIT}:level=disabled:attack=3:release=40:latency=1", "-c:a", "pcm_s16le", out])
        level = measure_loudness(out)[0]
        if abs(TARGET_LUFS - level) <= LUFS_TOLERANCE:
            break
    return level


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
    inside_a_tag = {i for i in range(1, len(words)) if words[i]["tag"] is not None and words[i]["tag"] == words[i - 1]["tag"]}
    break_at, size = fit_lines([w["text"] for w in words], CAPTION_CHARS, CAPTION_SIZE, inside_a_tag)
    parts = []
    for i, w in enumerate(words):
        if i:
            parts.append("\\N" if i == break_at else " ")
        text = escape_ass(w["text"])
        parts.append(f"{{\\c{COLOURS[w['colour']]}}}{text}{{\\c{WHITE}}}" if w["colour"] else text)
    return f"{{\\fs{size}{POP}}}" + "".join(parts)


def title_text(text, y, pop):
    words = text.split()
    break_at, size = fit_lines(words, TITLE_CHARS, TITLE_SIZE)
    pieces = [words] if break_at is None else [words[:break_at], words[break_at:]]
    return f"{{\\an5\\pos({CENTRE_X},{y})\\fs{size}{pop}}}" + "\\N".join(escape_ass(" ".join(p)) for p in pieces)


def dialogue(layer, start, end, style, text):
    return f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}"


def ass_document(events):
    fields = ("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
              "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
              "MarginR, MarginV, Encoding")

    def style(name, size, outline, alignment, margin_v):
        return (f"Style: {name},{FONT},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,{outline},"
                f"0,{alignment},{SAFE_LEFT},{SAFE_RIGHT},{margin_v},1")
    return "\n".join(["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2",
                      "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "", "[V4+ Styles]", fields,
                      style("Caption", CAPTION_SIZE, CAPTION_OUTLINE, 2, H - CAPTION_BOTTOM),
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
    events = [dialogue(1, 0, OPEN_CARD_S, "Title", title_text(open_card, OPEN_CARD_Y, OPEN_POP))] if open_card else []
    for slot in slots:
        if slot["visual"]["kind"] == "card":
            events.append(dialogue(0, slot["start"] / FPS, slot["end"] / FPS, "Title",
                                   title_text(slot["visual"]["text"], CARD_Y, POP)))
    return events


# --- The timeline ---

def plan_slots(lines, line_starts, total_frames):
    """Every visual's frames, in order. Cuts come from absolute times, so rounding never adds up to a drift. The loop
    ending takes the last frames of the voice's own time: adding them after it would make the video longer than the voice."""
    slots = []
    for n, line in enumerate(lines):
        a = round(line_starts[n] * FPS)
        b = round(line_starts[n + 1] * FPS) if n + 1 < len(lines) else total_frames
        for i, visual in enumerate(line["visuals"]):
            start, end = a + (b - a) * i // len(line["visuals"]), a + (b - a) * (i + 1) // len(line["visuals"])
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


def make_motion(kind, speed, frames):
    """A move as the window's zoom and how far across its free room it sits (0 left, 1 right), at start and end. A pan
    holds a slight zoom, which gives it room on any picture, even one with no width to spare."""
    seconds = max(frames - 1, 1) / FPS
    if kind.startswith("zoom"):
        amount = min(ZOOM_PER_S * speed * seconds, ZOOM_MAX)
        zoom = (1.0, 1.0 + amount) if kind == "zoom_in" else (1.0 + amount, 1.0)
        return {"kind": kind, "speed": round(speed, 3), "zoom": zoom, "across": (0.5, 0.5)}
    travel = min(PAN_PX_PER_S * speed * seconds, PAN_MAX_PX)
    fixed = W / (W - travel)
    return {"kind": kind, "speed": round(speed, 3), "zoom": (fixed, fixed),
            "across": (1.0, 0.0) if kind == "pan_left" else (0.0, 1.0)}


def return_motion(opening, opening_frames, frames):
    """The opening's first frames played backwards, so the Short's last frame is exactly its first."""
    p = min((frames - 1) / max(opening_frames - 1, 1), 1.0)
    at = lambda pair: pair[0] + (pair[1] - pair[0]) * p
    return {"kind": "back_to_first_frame", "speed": opening["speed"],
            "zoom": (at(opening["zoom"]), opening["zoom"][0]), "across": (at(opening["across"]), opening["across"][0])}


def assign_motions(slots, seed):
    """A different move on each image, seeded so a re-render of the same title looks the same."""
    rng = random.Random(seed)
    kinds = motion_kinds(rng)
    for slot in slots:
        if slot["visual"]["kind"] == "image" and not slot.get("loop_tail"):
            slot["motion"] = make_motion(next(kinds), rng.uniform(*SPEED_RANGE), slot["frames"])
    if slots[-1].get("loop_tail") and slots[0]["visual"]["kind"] == "image":
        slots[-1]["motion"] = return_motion(slots[0]["motion"], slots[0]["frames"], slots[-1]["frames"])


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
        if seconds > LONG_SLOT_S and not slot.get("loop_tail"):
            warnings.append(f"line {slot['line'] + 1}: {visual['source']} stays up {seconds:.1f} s; the target is about "
                            "2 s per visual, so give this line another one")
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
    """Crop to 9:16 at the framing asked for, enlarge, then move a window over it."""
    iw, ih = visual["size"]
    if iw * H > ih * W:
        crop_w, crop_h = round(ih * W / H), ih
        x, y = round((iw - crop_w) * visual["focus"]), 0
    else:
        crop_w, crop_h = iw, round(iw * H / W)
        x, y = 0, (ih - crop_h) // 2
    p = f"on/{max(frames - 1, 1)}"
    (z0, z1), (a0, a1) = motion["zoom"], motion["across"]
    return (f"crop={crop_w}:{crop_h}:{x}:{y},scale={W * CANVAS_SCALE}:{H * CANVAS_SCALE}:flags=lanczos:{COLOUR_OUT},"
            f"format=yuv444p,zoompan=z='{z0:.5f}+({z1 - z0:.5f})*{p}':x='({a0:.5f}+({a1 - a0:.5f})*{p})*(iw-iw/zoom)':"
            f"y='0.5*(ih-ih/zoom)':d={frames}:s={W}x{H}:fps={FPS},format=yuv420p,setsar=1")


def video_filter():
    """Fill the frame, cropping the sides, never a blurred background. tpad holds the last frame if the file runs out."""
    return (f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos:{COLOUR_OUT},crop={W}:{H},"
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
        inputs = ["-f", "image2", "-pattern_type", "none", "-i", visual["path"]]
        picture = image_filter(visual, slot["motion"], frames)
    else:
        inputs = ["-stream_loop", "-1"] if slot["fill"] == "loop" else []
        inputs += ["-ss", f"{slot['start_s']:.3f}", "-i", visual["path"]]
        picture = video_filter()
    ffmpeg([*inputs, "-an", "-sn", "-dn", "-vf", picture, "-frames:v", frames, *encode, *COLOUR_TAGS, out])


def music_graph(music, total_s):
    """The music's gain (to about 18 LU under the voice) and the filter that loops, trims and mixes it as input 2."""
    level = measure_loudness(music)[0]
    gain = MUSIC_LUFS - level if math.isfinite(level) else 0.0
    fade = max(total_s - 0.4, 0)
    return gain, (f"[2:a]atrim=0:{total_s:.3f},volume={gain:.2f}dB,afade=t=out:st={fade:.3f}:d=0.4,"
                  "aformat=channel_layouts=stereo[m];[1:a]aformat=channel_layouts=stereo[v];"
                  "[v][m]amix=inputs=2:normalize=0:duration=first[a]")


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
    if script["voice"] not in kokoro.get_voices():
        raise ScriptError(f"voice {script['voice']!r} doesn't exist. Voices: {', '.join(kokoro.get_voices())}")
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
        if not MIN_TOTAL_S <= voice_s <= MAX_TOTAL_S:
            warnings.append(f"the Short is {voice_s:.1f} s; the target is 60-70 s, and under {MIN_TOTAL_S} or over "
                            f"{MAX_TOTAL_S} is too far off")

        events = card_events(slots, script["open_card"]) + caption_events(words, total_s)
        (run_dir / "captions.ass").write_text(ass_document(events), encoding="utf-8")
        shutil.copy(FONT_FILE, work)
        font_problem = font_warning(work)
        if font_problem:
            warnings.append(font_problem)

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
        inputs = ["-f", "concat", "-safe", "0", "-i", "segments.txt", "-i", run_dir / "voice.wav"]
        graph = "[0:v]ass=../captions.ass:fontsdir=.,format=yuv420p[p]"
        audio_out = "1:a"
        music_gain = None
        if script["music"]:
            music_gain, mix = music_graph(script["music"], total_s)
            inputs += ["-stream_loop", "-1", "-i", script["music"]]
            graph += ";" + mix
            audio_out = "[a]"
        ffmpeg([*inputs, "-filter_complex", graph, "-map", "[p]", "-map", audio_out, "-frames:v", total_frames,
                "-r", FPS, *final_args, *COLOUR_TAGS, "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
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
    log = {"script": str(script_path), "title": script["title"], "voice": script["voice"], "speed": script["speed"],
           "encoder": encoder, "timing_method": method,
           "durations": {"voice_s": round(voice_s, 3), "video_s": round(video_s, 3), "frames": total_frames,
                         "lines": [{"line": n + 1, "start_s": round(s, 3)} for n, s in enumerate(line_starts)]},
           "loudness": {"voice_lufs": voice_lufs, "final_lufs": final_lufs, "final_true_peak_db": final_peak,
                        "music_gain_db": music_gain},
           "longest_quiet_s": round(quiet, 2), "words": len(words),
           "visuals": [{"line": s["line"] + 1, "source": s["visual"]["source"], "start_s": round(s["start"] / FPS, 3),
                        "seconds": round(s["frames"] / FPS, 3), "kind": s["visual"]["kind"],
                        "motion": s["motion"]["kind"] if "motion" in s else None,
                        "motion_speed": s["motion"]["speed"] if "motion" in s else None, "fill": s.get("fill"),
                        "loop_tail": bool(s.get("loop_tail"))} for s in slots],
           "per_second": [{"second": sec, "visual": at_second(sec)} for sec in range(math.ceil(total_s))],
           "warnings": warnings, "render_s": timing}
    (run_dir / "log.json").write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Run folder: {run_dir}")
    print(f"  {voice_s:.1f} s, {len(slots)} visuals, {final_lufs:.1f} LUFS, made in {timing['total']:.0f} s with {encoder}")
    for warning in warnings:
        print(f"  WARNING: {warning}")
    return run_dir


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
