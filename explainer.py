"""Turn a script file into a finished 1080x1920 explainer Short: Kokoro voice, captions timed to each word, a ducked music
bed, sound effects, fast cuts and a loop ending.

A script is a JSON file (see examples/nov10_preload_v2.json). Each line says something over one or more shots, which share
that line's time by their weights (equal unless a shot says otherwise). A shot is a video, an image or a text card,
written as a short string ("rockstar/x.mp4@0:42", "rockstar/x.jpg@0.7", "card:SOME TEXT") or as an object with more
choices: where the subject is ("focus"), a slide across the whole picture ("pan"), a bigger share ("weight"), the
layout ("framed" card or "full" screen), the card's shape ("card_aspect"), a pill "label" and "marks" on the picture.
The README lists them all. Everything is checked first, so a missing file or a bad setting stops before anything renders.

The look (PR 17, "Neon Getaway"): a landscape picture floats as a framed card over a blurred, darkened copy of itself;
a portrait one fills the screen. Text cards sit over a blurred picture too, never on black. Captions show one chunky
yellow word at a time.

Beats (PR 18): a shot may pick a beat type with "beat", so a Short mixes full-screen shots and cards instead of being
a row of square cards: full, lockon (a red box drops onto a region and the camera zooms at it), card, compare (two
stacked cards), snippet (a text card with a marker sweep), count and banner (a pill over footage). A line may call its
shots "beats" instead of "visuals". "end_pill" adds a small subscribe pill with a cursor click to the last 2 s. After
a render the pacing check prints the length, the visual changes, how many that is every 10 s and the longest gap.

Writes short.mp4, voice.wav, captions.ass and log.json to C:\\AI\\gta\\explainers\\<slug>\\<YYYYMMDD-HHMMSS>\\.

Needs .venv-tts (requirements-explainer.txt), the two Kokoro model files and ffmpeg/ffprobe on PATH; see the README.

Usage:  .venv-tts\\Scripts\\python.exe explainer.py script.json [--root FOLDER] [--out FOLDER]
        .venv-tts\\Scripts\\python.exe explainer.py --pacing RUN_FOLDER     (a finished run's pacing check again)
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
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
import zlib
from datetime import datetime
from pathlib import Path

from clip import RANGE_FIX_ARGS, needs_range_fix
from hooks import ass_time, escape_ass

HERE = Path(__file__).resolve().parent
ROOT = Path(r"C:\AI\gta")
MODEL_DIR = ROOT / "models" / "kokoro"
MODEL, VOICES = MODEL_DIR / "kokoro-v1.0.onnx", MODEL_DIR / "voices-v1.0.bin"
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1"
FONT_FILE, FONT = HERE / "fonts" / "Anton-Regular.ttf", "Anton"
CHUNKY_FILE, CHUNKY = HERE / "fonts" / "LilitaOne-Regular.ttf", "Lilita One"   # captions, cards, labels (SIL OFL)
FONT_FILES = (FONT_FILE, CHUNKY_FILE)
FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")

W, H, FPS = 1080, 1920, 30
MIN_TOTAL_S, MAX_TOTAL_S = 34, 39    # the target runtime since PR 18 (it was 40-50); outside it warns
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
WHOOSH_PITCH = {"low": 0.62, "high": 1.5}                 # a compare's two whooshes, next to the usual one
LOCK_SFX_S, LOCK_DB, CLICK_SFX_S, CLICK_DB = 0.3, -17.0, 0.25, -13.0  # the lock-on click and the subscribe pill's click
SFX_LOWPASS_HZ, SFX_SPACING_S, SFX_EARLIEST_S = 8000, 0.3, 0.1

# Captions. Shorts covers the top 380, bottom 380, right 120 and left 60 px (the same zones as hooks.py), so x centres
# on 510. Anton has one weight, so bold stays 0: bold would ask libass for a faked one.
CENTRE_X, SAFE_LEFT, SAFE_RIGHT = 510, 60, 120
# libass sizes a font by its full line height, and Anton's is tall: at size 176 a capital is only about 45 px wide.
CAPTION_SIZE, CAPTION_OUTLINE, CAPTION_BOTTOM = 176, 12, 1500
CAPTION_CHARS, GROUP_CHARS = 16, 28  # most characters on a line, then in a chunk (which may take 2 lines)
MIN_WORDS, MAX_WORDS, CAPTION_LINGER_S = 2, 4, 0.25
TITLE_SIZE, TITLE_OUTLINE, TITLE_CHARS, OPEN_CARD_S = 260, 14, 13, 2.0
OPEN_CARD_Y, CARD_Y = 650, 940       # the middle of the open card's text, and of a card's (no caption shows under it)
WHITE, BLACK = "&HFFFFFF&", "&H000000&"


def ass_colour(rgb):
    """#RRGGBB as ASS writes it: blue, green, red."""
    return "&H%02X%02X%02X&" % (rgb[2], rgb[1], rgb[0])


# The palette. "green" keeps its name in scripts (tags and *card words*) but draws cyan since PR 17.
CYAN, PINK, RED_RGB, YELLOW_RGB = (0, 229, 255), (255, 46, 147), (255, 42, 42), (255, 230, 0)
COLOURS = {"green": ass_colour(CYAN), "red": ass_colour(RED_RGB)}
HIGHLIGHT = ass_colour(YELLOW_RGB)   # the word being said. Tagged words keep their cyan or red.
# A soft shadow under the phrase captions' black outline, so they also read on a bright, busy picture: px, and ASS alpha
# (00 solid, FF clear).
CAPTION_SHADOW, SHADOW_ALPHA = 5, "70"
POP = "\\fscx88\\fscy88\\t(0,70,\\fscx106\\fscy106)\\t(70,130,\\fscx100\\fscy100)"
OPEN_POP = POP + "\\fad(0,120)"
POP_PEAK = 1.06                      # how big POP grows text for a moment: text is fitted so it stays in the safe area then
# Caption styles. "word" (the default): one word at a time in Lilita One, yellow with a thick black outline, its capitals
# centred on WORD_Y, which sits over the lower part of a square card. "phrase": the PR 16 captions, 2-4 Anton words with
# the spoken one yellow, bottom at CAPTION_BOTTOM.
CAPTION_STYLES = ("word", "phrase")
WORD_SIZE, WORD_OUTLINE, WORD_SHADOW, WORD_Y = 170, 13, 6, 1260
WORD_POP = "\\fscx80\\fscy80\\t(0,80,\\fscx108\\fscy108)\\t(80,150,\\fscx100\\fscy100)"
WORD_POP_PEAK = 1.08
SAFE_WIDTH = 900                     # x 60-960: a word or card line, its outline and its shadow centred on CENTRE_X
# Where each caption style can draw, top to bottom, outline and shadow in (explainertest.py measures both): labels
# keep LABEL_GAP away from it.
CAPTION_BANDS = {"word": (1185, 1345), "phrase": (1130, 1510)}
# Card markup: | starts a new line, ~words~ get a red bar through them, *words* are cyan ("green"). A number at the
# start of the first line counts up from 0 over COUNT_S. Text is in Lilita One, and every position comes from libass
# itself (measure()), so the bar lands on the words. The bar is drawn, because libass's own strikeout is a hairline.
CARD_MARKS, MAX_CARD_LINES = "~*", 3
CARD_TEXT_SIZE, OPEN_TEXT_SIZE = 300, 220       # the most: wider or taller text is made smaller to fit
TITLE_OUTLINE_SHARE, TITLE_SHADOW_SHARE = 0.047, 0.04
BAR_HEIGHT, BAR_PAD, BAR_OUTLINE, BAR_TILT = 0.1, 0.1, 6, 3    # of the size, of the size each side, px, degrees
BAR_DELAY_MS, BAR_GROW_MS = 250, 200                           # the old text shows first, then the bar strikes it
COUNT_S = 0.6

# Layouts. "framed": the picture as a card with a white border and a hard shadow, scaling in from 96 % over 0.25 s, over
# a blurred, darkened copy of itself that zooms very slowly. "full": the whole screen, cropped around the focus (PR 15).
# A shot without a layout takes the script's, and a script without one frames landscape pictures and fills the screen
# with portrait ones.
LAYOUTS = ("framed", "full")
CARD_ASPECTS = {"1:1": (1, 1), "4:5": (4, 5), "16:9": (16, 9)}
DEFAULT_CARD_ASPECT = "1:1"
CARD_BOX, CARD_MIDDLE_Y = 900, 960   # the card fits in a square this big (border in), x 60-960, centred in the safe area
CARD_BORDER, CARD_RADIUS = 10, 22
CARD_SHADOW_DX, CARD_SHADOW_DY, CARD_SHADOW_BLUR, CARD_SHADOW_ALPHA = 12, 14, 3, 0.8
CARD_MARGIN = 28                     # room on every side of the card, in its layer, for the shadow
CARD_IN_S, CARD_IN_FROM = 0.25, 0.96
SAFE_TOP, SAFE_BOTTOM = 380, 1540
# The background: a 9:16 crop, blurred (sigma in screen pixels), more saturated, and brought to BG_LEVEL average
# brightness (lifted at most BG_MAX_GAIN), so a dark picture doesn't go black and a bright one doesn't glare.
BG_BLUR, BG_SATURATION, BG_LEVEL, BG_MAX_GAIN, BG_ZOOM_PER_S = 36, 1.35, 0.30, 1.3, 0.012
BG_SMALL = (270, 480)                # blurred at this size, which is cheap, then scaled up
# The colour grade on every picture (the card's, or the full screen): a little more contrast and colour, and a soft
# vignette over the whole frame. "grade": false turns both off.
GRADE, VIGNETTE = "eq=contrast=1.09:saturation=1.25", "vignette=angle=0.35"

# Labels: a pill on the card's top or bottom edge (or near the screen's top or bottom in "full", or on a text card),
# popping in with an overshoot just after the shot starts. Colour has a meaning.
GREY, INK = (124, 130, 142), (20, 20, 28)
LABEL_STYLES = {"info": CYAN, "no": RED_RGB, "number": PINK, "grey": GREY}
LABEL_POSITIONS = ("top", "bottom")
LABEL_SIZE, LABEL_PAD_X, LABEL_PAD_Y, LABEL_BORDER, LABEL_SHADOW = 80, 36, 24, 8, 7
LABEL_DELAY_S, LABEL_GAP = 0.22, 14
LABEL_POP = "\\fscx70\\fscy70\\t(0,140,\\fscx112\\fscy112)\\t(140,260,\\fscx100\\fscy100)"
LABEL_POP_PEAK = 1.12
FULL_LABEL_Y = {"top": 470, "bottom": 1420}

# Marks on the picture, at a spot given as a share of the picture's width and height: a white cross on a red disc, a
# white tick on a cyan one (both ringed in white), a red arrow pointing down at the spot, or a red ring around it.
MARK_ICONS = ("x", "check", "arrow", "circle")
MARK_SIZE, MARK_EARLIEST_S = 92, 0.3

# Beats (PR 18). A shot may pick a beat type with "beat": how it fills the screen and what moves in it. A shot without
# one is drawn as before. "full": the whole screen, zooming slowly at its focus. "card": the framed card above (or a
# text card). "count": a text card whose number counts up. The four below have their own settings.
BEATS = ("full", "lockon", "card", "compare", "snippet", "count", "banner")
BEAT_KEYS = {"full": {"file", "at", "focus", "pan", "weight", "label", "marks"},
             "lockon": {"file", "at", "weight", "box", "label"},
             "card": {"file", "card", "at", "focus", "pan", "weight", "card_aspect", "label", "marks", "bg"},
             "compare": {"top", "bottom", "weight"},
             "snippet": {"text", "highlight", "weight", "bg", "label"},
             "count": {"card", "weight", "bg", "label"},
             "banner": {"file", "at", "focus", "weight", "text", "style"}}
# "lockon": a red box drops onto a region of the picture (given in the picture's own pixels) LOCK_SNAP_S after the cut,
# with a small tag on its corner, and the camera zooms at it: fast at first, then a slow creep. The zoom is the largest
# up to LOCK_ZOOM_MAX that fits the region in LOCK_ROOM and keeps it, and its tag, inside the safe area.
LOCK_SNAP_S, LOCK_SNAP_FRAMES, LOCK_SNAP_FROM = 0.35, 4, 1.5      # when it lands; frames it takes; its size as it starts
LOCK_ZOOM_S, LOCK_ZOOM_MIN, LOCK_ZOOM_MAX, LOCK_CREEP_PER_S = 0.9, 1.1, 1.6, 0.012
LOCK_ROOM, LOCK_TARGET_Y = (760, 600), 815
LOCK_STROKE, LOCK_EDGE, LOCK_CORNER = 8, 13, 10                   # the red line, the white edge under it, its corners
LOCK_TAG_SIZE, LOCK_TAIL_ZOOM = 56, 0.03
# "compare": two 16:9 cards, one above the other. The second slides up from below COMPARE_SECOND_S after the first,
# over COMPARE_SLIDE_S. The top card's label sits on its top edge and the bottom card's on its bottom edge, and the
# word captions move to the seam between the cards while it is up, so neither picture is covered.
COMPARE_ASPECT, COMPARE_GAP, COMPARE_SECOND_S, COMPARE_SLIDE_S, COMPARE_LABEL_SIZE = "16:9", 26, 0.8, 0.3, 64
# "snippet": a white card of plain dark text, at most SNIPPET_MAX_WORDS words, as large as fits. A yellow marker sweeps
# left to right behind the chosen words. It is plain styled text on purpose: it never imitates a web page or an article.
SNIPPET_MAX_WORDS, SNIPPET_SIZE, SNIPPET_MIN_SIZE, SNIPPET_LINE = 18, 104, 60, 1.16
SNIPPET_PAD_X, SNIPPET_PAD_Y, SNIPPET_RADIUS, SNIPPET_TOP, SNIPPET_MIDDLE_Y = 56, 50, 30, 445, 800
SWEEP_DELAY_S, SWEEP_PX_PER_S, SWEEP_MIN_S, SWEEP_GAP_S = 0.4, 1500, 0.25, 0.1
# "banner": a pill of 1-3 words over full-screen footage, up for BANNER_S (or the whole shot, if that is shorter).
BANNER_WORDS, BANNER_SIZE, BANNER_MIN_SIZE, BANNER_Y, BANNER_S, BANNER_DELAY_S = 3, 124, 48, 800, 1.5, 0.1
# The ending's subscribe pill ("end_pill"): small and white, up for the last PILL_S. A cursor slides onto it and clicks
# PILL_CLICK_S before the end; it has gone PILL_OUT_S before the end, so the last frame is still the first frame.
PILL_SIZE, PILL_PAD, PILL_MAX_WIDTH, PILL_CHARS = 46, (40, 26), 760, 40
PILL_S, PILL_CLICK_S, PILL_OUT_S, PILL_Y = 2.0, 1.1, 0.3, {"bottom": 1470, "top": 446}
PILL_PRESS = "\\fscx91\\fscy91\\t(0,150,\\fscx100\\fscy100)"
# The pacing checker (PR 18): a visual change is a cut, or something arriving inside a shot (a lock-on box, the
# second card of a compare, a marker sweep, a mark, the subscribe pill). Changes closer than CHANGE_MERGE_S to the
# one before are the same moment to the eye and count once: a lock-on box or a marker that follows its cut by 0.4 s
# is part of that cut, and a compare's second card, 0.8 s in, is a change of its own. Counting each of those would
# make a script look busier than a channel counted by its cuts. The targets come from a top GTA 6 Shorts channel:
# 4-5 changes every 10 s, never 3 s without one.
CHANGE_MERGE_S, MIN_CHANGES_PER_10_S, MAX_CHANGES_PER_10_S, MAX_GAP_S = 0.5, 4.0, 6.0, 3.0
TARGET_WORDS = (95, 105)

# Timeline.
LOOP_FRAMES = 45                     # the last 1.5 s shows the first visual again
# The loop ending never takes a picture's whole time: every shot it squeezes keeps at least this much, taken from the
# shots before it. 1.0 s is twice CHANGE_MERGE_S, so the picture counts as a change of its own; it is the length under
# which the last line's pictures already warned; and Rockstar's shortest loops last that long.
MIN_SHOWN_FRAMES = 30
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
SHOT_KEYS = {"file", "card", "at", "focus", "pan", "weight", "layout", "card_aspect", "label", "marks", "bg", "beat"}
CARD_KEYS = {"card", "weight", "bg", "label"}
SCRIPT_KEYS = {"title", "voice", "speed", "open_card", "music", "music_volume", "sfx", "lines", "layout",
               "caption_style", "grade", "end_pill"}
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


def parse_card(text, source, weight, extra=None):
    text = text.strip().upper()
    if not text:
        raise ScriptError("a card with no text")
    card_lines(text)
    visual = {"source": source, "kind": "card", "text": text}
    if weight != 1.0:
        visual["weight"] = weight
    visual.update(extra or {})
    return visual


def parse_label(value, where):
    """A label as {"text", "style", "pos"}: text alone is an info label on top. Its text is shown in capitals, and a |
    in it is drawn as it is ("GTA V | 2013"), not a line break."""
    if isinstance(value, str):
        value = {"text": value}
    if not isinstance(value, dict):
        raise ScriptError(f'{where}: label is its text, or {{"text": ..., "style": ..., "pos": ...}}')
    unknown = sorted(set(value) - {"text", "style", "pos"})
    if unknown:
        raise ScriptError(f"{where}: a label has text, style and pos, not {', '.join(map(repr, unknown))}")
    text = value.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ScriptError(f"{where}: a label needs its text")
    style, pos = value.get("style", "info"), value.get("pos", "top")
    if style not in LABEL_STYLES:
        raise ScriptError(f"{where}: label style is info (cyan), no (red), number (pink) or grey")
    if pos not in LABEL_POSITIONS:
        raise ScriptError(f"{where}: label pos is top or bottom")
    return {"text": " ".join(text.split()).upper(), "style": style, "pos": pos}


def parse_marks(value, where):
    """Marks as a list of {"icon", "x", "y", "at"}: x and y are where on the picture, as shares of its width and height
    (0-1), and at is when it appears, in seconds from the shot's start."""
    if not isinstance(value, list) or not value:
        raise ScriptError(f'{where}: marks is a list such as [{{"icon": "x", "x": 0.4, "y": 0.3, "at": 0.5}}]')
    marks = []
    for mark in value:
        if not isinstance(mark, dict) or set(mark) - {"icon", "x", "y", "at"}:
            raise ScriptError(f"{where}: each mark has icon, x, y and at, and nothing else")
        icon = mark.get("icon", "x")
        if icon not in MARK_ICONS:
            raise ScriptError(f"{where}: a mark's icon is {', '.join(MARK_ICONS)}")
        x, y = parse_share(mark.get("x"), {}), parse_share(mark.get("y"), {})
        if x is None or y is None:
            raise ScriptError(f"{where}: a mark's x and y are where it goes, 0 to 1 across and down the picture")
        at = mark.get("at", MARK_EARLIEST_S)
        if isinstance(at, bool) or not isinstance(at, (int, float)) or at < 0:
            raise ScriptError(f"{where}: a mark's at is when it appears, in seconds from the shot's start")
        marks.append({"icon": icon, "x": x, "y": y, "at": max(float(at), MARK_EARLIEST_S)})
    return marks


def parse_file(name, at, focus, pan, weight, root, source, extra=None):
    """An image or video, checked against the real file. at is a video's start; focus and pan are already read. extra
    holds the shot's own layout, card_aspect, label and marks, already read."""
    extra = extra or {}
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
    # A PS5 .webm is stored 1920x1088 with the bottom 8 rows cropped by the container, and the decoder gives 1920x1080.
    cut = next((d for d in picture.get("side_data_list", []) if d.get("side_data_type") == "Frame Cropping"), {})
    size = (int(picture["width"]) - int(cut.get("crop_left", 0)) - int(cut.get("crop_right", 0)),
            int(picture["height"]) - int(cut.get("crop_top", 0)) - int(cut.get("crop_bottom", 0)))
    shape = {"weight": weight} if weight != 1.0 else {}
    across, down = focus or (0.5, 0.5)
    if ext in IMAGE_EXTS:
        if at is not None:
            raise ScriptError(f"{source}: at is a video's start time; an image has none")
        if pan is not None and extra.get("layout") != "framed" and size[0] * H <= size[1] * W:
            raise ScriptError(f"{source}: pan slides across a wide picture, and this one is no wider than the screen")
        return {"source": source, "kind": "image", "path": path, "focus": across, "focus_y": down,
                "pan": pan, "size": size, **shape, **extra}
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
            "focus": across, "focus_y": down, "size": size, **shape, **extra}


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


def parse_weight(raw):
    weight = raw.get("weight", 1.0)
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 < weight <= MAX_WEIGHT:
        raise ScriptError(f"{json.dumps(raw)}: weight is its share of the line, a number above 0 and up to {MAX_WEIGHT:g}")
    return float(weight)


def parse_behind(raw, where, root):
    """A text card's own label and bg, read."""
    more = {}
    if "label" in raw:
        more["label"] = parse_label(raw["label"], where)
    if "bg" in raw:
        if not isinstance(raw["bg"], str) or raw["bg"].startswith("card:"):
            raise ScriptError(f"{where}: bg is the picture behind the card, an image or video such as rockstar/x.jpg")
        more["bg"] = parse_visual(raw["bg"], root)
    return more


def parse_beat(raw, root):
    """A shot that picks a beat type (PR 18). full, card and count are the shots there were before, pinned to one
    look; lockon, compare, snippet and banner bring their own settings. ScriptError says what is wrong."""
    beat = raw["beat"]
    if beat not in BEATS:
        raise ScriptError(f"{json.dumps(raw)}: beat is one of {', '.join(BEATS)}")
    unknown = sorted(k for k in raw if k != "beat" and k not in BEAT_KEYS[beat])
    if unknown:
        raise ScriptError(f"{json.dumps(raw)}: a {beat} beat takes {', '.join(sorted(BEAT_KEYS[beat]))}, not "
                          f"{', '.join(map(repr, unknown))}")
    rest = {k: v for k, v in raw.items() if k != "beat"}

    def need(*keys):
        missing = [k for k in keys if k not in raw]
        if missing:
            raise ScriptError(f"{json.dumps(raw)}: a {beat} beat needs {' and '.join(missing)}")

    if beat == "full":
        need("file")
        visual = parse_shot_object({**rest, "layout": "full"}, root)
    elif beat == "card":
        if ("file" in raw) == ("card" in raw):
            raise ScriptError(f"{json.dumps(raw)}: a card beat has a file (a framed picture) or a card (its text), one "
                              "of the two")
        visual = parse_shot_object({"layout": "framed", **rest} if "file" in raw else rest, root)
    elif beat == "count":
        need("card")
        visual = parse_shot_object(rest, root)
        if split_number(title_lines(visual["text"]))[1] is None:
            raise ScriptError(f"{visual['source']}: a count beat's card starts with the number that counts up, such as "
                              "212 GB")
    elif beat == "banner":
        need("file", "text")
        text = raw["text"]
        if not isinstance(text, str) or not 1 <= len(text.split()) <= BANNER_WORDS:
            raise ScriptError(f"{json.dumps(raw)}: a banner's text is 1-{BANNER_WORDS} words, such as THE CATCH")
        style = raw.get("style", "number")
        if style not in LABEL_STYLES:
            raise ScriptError(f"{json.dumps(raw)}: a banner's style is info (cyan), no (red), number (pink) or grey")
        visual = parse_shot_object({k: v for k, v in rest.items() if k not in ("text", "style")} | {"layout": "full"}, root)
        visual["banner"] = {"text": " ".join(text.split()).upper(), "style": style, "pos": "top", "font": BANNER_SIZE,
                            "y": BANNER_Y}
    elif beat == "lockon":
        need("file", "box")
        visual = parse_shot_object({k: v for k, v in rest.items() if k not in ("box", "label")} | {"layout": "full"}, root)
        visual["lock"] = parse_lock(raw, visual)
        visual["source"] += " (lock-on)"
    elif beat == "compare":
        need("top", "bottom")
        visual = parse_compare(raw, root)
    else:
        need("text")
        visual = parse_snippet(raw, root)
    visual["beat"] = beat
    return visual


def parse_lock(raw, visual):
    """A lock-on's region, [x, y, width, height] in the picture's own pixels, and its tag. The shot's crop centres on
    the region, so it takes no focus."""
    name, box = raw["file"], raw["box"]
    iw, ih = visual["size"]
    good = (isinstance(box, list) and len(box) == 4
            and all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in box))
    if not good or box[2] < 8 or box[3] < 8 or box[0] < 0 or box[1] < 0 or box[0] + box[2] > iw or box[1] + box[3] > ih:
        raise ScriptError(f"{name}: box is the region to lock on to, [x, y, width, height] in the picture's own pixels, "
                          f"and this picture is {iw}x{ih}")
    x, y, w, h = map(float, box)
    visual["focus"], visual["focus_y"] = (x + w / 2) / iw, (y + h / 2) / ih
    lock = {"box": (x, y, w, h), "tag": None}
    if "label" in raw:
        tag = parse_label(raw["label"], name)
        if isinstance(raw["label"], str) or "style" not in raw["label"]:
            tag["style"] = "no"                      # red, like the box
        lock["tag"] = {**tag, "font": LOCK_TAG_SIZE, "border": 5, "shadow": 4}
    return lock


def parse_compare(raw, root):
    """A compare beat's two pictures, each a framed 16:9 card. A label without a style is grey on top and pink below."""
    sides = {}
    for side, style, pos in (("top", "grey", "top"), ("bottom", "number", "bottom")):
        part = raw[side]
        if not isinstance(part, dict) or not isinstance(part.get("file"), str):
            raise ScriptError(f"{json.dumps(raw)}: a compare beat's {side} is a picture, such as "
                              '{"file": "rockstar/x.jpg", "label": "GTA V"}')
        extra = sorted(set(part) - {"file", "at", "focus", "label"})
        if extra:
            raise ScriptError(f"{part['file']}: a compare beat's {side} takes file, at, focus and label, not "
                              f"{', '.join(map(repr, extra))}")
        visual = parse_shot_object({**part, "layout": "framed", "card_aspect": COMPARE_ASPECT}, root)
        if visual.get("label"):
            if isinstance(part["label"], str) or "style" not in part["label"]:
                visual["label"]["style"] = style
            visual["label"].update(pos=pos, font=COMPARE_LABEL_SIZE)
        sides[side] = visual
    weight = parse_weight(raw)
    names = " / ".join(Path(sides[side]["path"]).name for side in ("top", "bottom"))
    return {"source": f"compare: {names}", "kind": "compare", **sides, **({"weight": weight} if weight != 1.0 else {})}


def parse_snippet(raw, root):
    """A snippet beat: its words as typed, and the word ranges the marker sweeps."""
    text = raw["text"]
    if not isinstance(text, str) or not text.split():
        raise ScriptError(f"{json.dumps(raw)}: a snippet's text is the words on its card")
    words = text.split()
    where = "snippet:" + " ".join(words)
    if len(words) > SNIPPET_MAX_WORDS:
        raise ScriptError(f"{where}: a snippet is at most {SNIPPET_MAX_WORDS} words, to be read in a second, and this "
                          f"has {len(words)}")
    marked = raw.get("highlight", [])
    marked = [marked] if isinstance(marked, str) else marked
    if not isinstance(marked, list) or not all(isinstance(m, str) and m.split() for m in marked):
        raise ScriptError(f"{where}: highlight is a list of words or phrases from the snippet's text")
    keys, spans = [word_key(w) for w in words], []
    for phrase in marked:
        places = find_phrase(keys, phrase)
        if not places:
            raise ScriptError(f"{where}: the highlight {phrase!r} isn't in the snippet's text")
        spans.append((places[0], places[0] + len(phrase.split())))
    spans.sort()
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise ScriptError(f"{where}: two highlights share a word")
    weight = parse_weight(raw)
    return {"source": where, "kind": "snippet", "text": " ".join(words), "words": words, "highlight": spans,
            **({"weight": weight} if weight != 1.0 else {}), **parse_behind(raw, where, root)}


def parse_shot_object(raw, root):
    if "beat" in raw:
        return parse_beat(raw, root)
    unknown = sorted(k for k in raw if k not in SHOT_KEYS)
    if unknown:
        raise ScriptError(f"{json.dumps(raw)}: unknown setting {', '.join(map(repr, unknown))}")
    if ("file" in raw) == ("card" in raw):
        raise ScriptError(f"{json.dumps(raw)}: a shot has a file or a card, one of the two")
    weight = parse_weight(raw)
    if "card" in raw:
        extra = sorted(k for k in raw if k not in CARD_KEYS)
        if extra:
            raise ScriptError(f"{json.dumps(raw)}: a card takes only its text, a weight, a bg and a label, not "
                              f"{', '.join(extra)}")
        if not isinstance(raw["card"], str):
            raise ScriptError(f"{json.dumps(raw)}: card is its text")
        where = "card:" + raw["card"].strip()
        return parse_card(raw["card"], where, weight, parse_behind(raw, where, root))
    name = raw["file"]
    if not isinstance(name, str) or not name.strip():
        raise ScriptError(f"{json.dumps(raw)}: file is a path such as rockstar/x.jpg")
    if "bg" in raw:
        raise ScriptError(f"{name}: bg is for cards; a picture's background is a blurred copy of itself")
    extra = {}
    if "layout" in raw:
        if raw["layout"] not in LAYOUTS:
            raise ScriptError(f"{name}: layout is framed (a card over a blurred copy) or full (the whole screen)")
        extra["layout"] = raw["layout"]
    if "card_aspect" in raw:
        if raw["card_aspect"] not in CARD_ASPECTS:
            raise ScriptError(f"{name}: card_aspect is {', '.join(CARD_ASPECTS)}")
        if raw.get("layout") == "full":
            raise ScriptError(f"{name}: card_aspect is the framed card's shape, and this shot fills the screen")
        extra["card_aspect"] = raw["card_aspect"]
    if "label" in raw:
        extra["label"] = parse_label(raw["label"], name)
    if "marks" in raw:
        extra["marks"] = parse_marks(raw["marks"], name)
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
    return parse_file(name, at, focus, pan, weight, root, source, extra)


def resolve_layout(visual, default):
    """Settle a picture's layout (its own; framed if it sets a card_aspect; else the script's; else framed if it is
    landscape and full if not) and its card's shape, and check a pan has room in the window it slides. ScriptError
    says what is wrong."""
    if visual["kind"] in ("card", "snippet"):
        return
    if visual["kind"] == "compare":
        for side in ("top", "bottom"):
            resolve_layout(visual[side], "framed")
        return
    iw, ih = visual["size"]
    layout = (visual.get("layout") or ("framed" if "card_aspect" in visual else default)
              or ("framed" if iw > ih else "full"))
    visual["layout"] = layout
    if layout == "framed":
        visual.setdefault("card_aspect", DEFAULT_CARD_ASPECT)
        aw, ah = CARD_ASPECTS[visual["card_aspect"]]
        visual["aspect"] = (aw, ah)
        if visual.get("pan") and iw * ah <= ih * aw:
            raise ScriptError(f"{visual['source']}: pan slides the card's window across the picture, and a "
                              f"{visual['card_aspect']} card already shows its whole width: use card_aspect 1:1 or 4:5")
    elif visual.get("pan") and iw * H <= ih * W:
        raise ScriptError(f"{visual['source']}: pan slides across a wide picture, and this one is no wider than the screen")


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
    layout = script.get("layout")
    if layout is not None and layout not in LAYOUTS:
        problems.append("layout is framed (pictures as cards over a blurred copy) or full (the whole screen); without "
                        "it, landscape pictures are framed and portrait ones fill the screen")
        layout = None
    caption_style = script.get("caption_style", "word")
    if caption_style not in CAPTION_STYLES:
        problems.append("caption_style is word (one word at a time) or phrase (2-4 words, the spoken one yellow)")
        caption_style = "word"
    grade = script.get("grade", True)
    if not isinstance(grade, bool):
        problems.append("grade is true (a little more contrast and colour, and a soft vignette) or false (none)")
        grade = True

    end_pill = script.get("end_pill")
    if end_pill is not None and (not isinstance(end_pill, str) or not end_pill.strip()
                                 or len(" ".join(end_pill.split())) > PILL_CHARS):
        problems.append(f"end_pill is the subscribe pill's text, up to {PILL_CHARS} characters, such as "
                        '"@NeonGetaway \u2022 Subscribe", or null for none')
        end_pill = None

    lines = script.get("lines")
    if not isinstance(lines, list) or not lines:
        problems.append("lines is missing or empty")
        lines = []
    parsed = []
    for n, line in enumerate(lines, 1):
        if not isinstance(line, dict):
            problems.append(f"line {n}: must be an object with say and visuals")
            continue
        problems += [f"line {n}: unknown setting {k!r}" for k in line
                     if k not in {"say", "visuals", "beats", "green", "red"}]
        if "visuals" in line and "beats" in line:
            problems.append(f"line {n}: beats is another name for visuals; use one of the two")
        say = line.get("say")
        if not isinstance(say, str) or not spoken_tokens(say):
            problems.append(f"line {n}: say is missing or has nothing to speak")
            say = ""
        raws = line.get("visuals", line.get("beats"))
        if not isinstance(raws, list) or not raws:
            problems.append(f"line {n}: needs at least one visual")
            raws = []
        visuals = []
        for raw in raws:
            try:
                visual = parse_visual(raw, root)
                resolve_layout(visual, layout)
                visuals.append(visual)
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
    notes, pill = [], None
    if not problems:
        placed, notes = place_visuals(parsed, caption_style)
        problems += placed
    if not problems and end_pill:
        pill = {"text": " ".join(end_pill.split()), "style": "info", "pos": "bottom", "font": PILL_SIZE, "pad": PILL_PAD,
                "fill": (255, 255, 255), "ink_colour": INK, "border": 0, "shadow": 6, "text_border": 0, "round": 0.5}
        measure_labels([pill])
        if pill["size"][0] * LABEL_POP_PEAK > PILL_MAX_WIDTH:
            problems.append(f"end_pill {pill['text']!r} is too wide to stay small; shorten it")
    if problems:
        raise ScriptError("the script needs fixing:\n  " + "\n  ".join(problems))
    return {"title": title, "slug": re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-"), "voice": voice, "speed": speed,
            "open_card": open_card.strip().upper() if open_card else None, "music": music,
            "music_volume": float(music_volume), "sfx": sfx, "lines": parsed, "layout": layout,
            "caption_style": caption_style, "grade": grade, "notes": notes, "end_pill": pill}


def place_visuals(lines, caption_style):
    """Give each text card a picture to blur behind it, each framed picture its card, and each label its place, so
    that no label touches the captions. Returns problems (a label too wide) and warnings (a label moved)."""
    problems, warnings = [], []
    order = [v for line in lines for v in line["visuals"]]
    pictures = [i for i, v in enumerate(order) if picture_of(v)]
    for i, v in enumerate(order):
        if v["kind"] in ("card", "snippet") and "bg" not in v:
            after, before = [j for j in pictures if j > i], [j for j in pictures if j < i]
            if not after and not before:
                problems.append(f"{v['source']}: a card shows over a blurred picture, and this script has none: give "
                                "it a bg")
                continue
            v["bg"] = picture_of(order[after[0] if after else before[-1]])   # the next picture, else the one before
    measure_labels([label for v in order for label in labels_of(v)]
                   + [v["banner"] for v in order if v.get("banner")]
                   + [v["lock"]["tag"] for v in order if v.get("lock") and v["lock"]["tag"]])
    for v in order:
        if v["kind"] == "compare":
            problems += place_compare(v)
            continue
        if v["kind"] == "snippet":
            problems += place_snippet(v, caption_style)
            continue
        if v.get("lock"):
            problems += place_lock(v, caption_style, warnings)
        if v.get("banner"):
            problems += fit_banner(v["banner"], v["source"])
        label = v.get("label")
        if label and label["size"][0] + 2 * LABEL_BORDER > CARD_BOX:
            problems.append(f"{v['source']}: the label {label['text']!r} is too wide to read at its size; shorten it")
            continue
        if v["kind"] != "card" and v["layout"] == "framed":
            v["frame"] = card_frame(v, label, caption_style, warnings)
        elif label and v["kind"] != "card":
            place_full_label(v, label, caption_style, warnings)
        elif label:
            label["y"] = FULL_LABEL_Y[label["pos"]]          # no caption shows over a text card
        if v.get("marks"):
            problems += place_marks(v, warnings)
    return problems, warnings


def place_marks(visual, warnings):
    """Where each mark lands on screen: its spot on the picture, through the crop the shot shows (a shot with marks
    holds still, so it stays put)."""
    if visual.get("pan"):
        return [f"{visual['source']}: marks stay on their spot, so a shot with marks can't pan"]
    x, y, cw, ch = crop_box(visual)[:4]
    iw, ih = visual["size"]
    sx, sy, sw, sh = visual["frame"]["content"] if visual["layout"] == "framed" else (0, 0, W, H)
    problems = []
    for mark in visual["marks"]:
        u, v = (mark["x"] * iw - x) / cw, (mark["y"] * ih - y) / ch
        if not (0 <= u <= 1 and 0 <= v <= 1):
            problems.append(f"{visual['source']}: the mark at x {mark['x']:g}, y {mark['y']:g} is outside the part of "
                            "the picture this shot shows")
            continue
        mark["screen"] = (sx + u * sw, sy + v * sh)
        if not (SAFE_LEFT <= mark["screen"][0] <= W - SAFE_RIGHT and SAFE_TOP <= mark["screen"][1] <= SAFE_BOTTOM):
            warnings.append(f"{visual['source']}: the mark at x {mark['x']:g}, y {mark['y']:g} lands where Shorts "
                            "draws its buttons or titles")
    return problems


def label_reach(label):
    """How far a label's pill reaches above and below its middle: its border, and its shadow below."""
    half = label["size"][1] / 2 + label.get("border", LABEL_BORDER)
    return half, half + label.get("shadow", LABEL_SHADOW)


def picture_of(visual):
    """The picture a text card can blur behind itself: a picture's own, or a compare's top one. None for a text card."""
    if visual["kind"] in ("image", "video"):
        return visual
    return visual["top"] if visual["kind"] == "compare" else None


def labels_of(visual):
    """A shot's label pills: its own, or a compare's two."""
    sides = [visual[side] for side in ("top", "bottom")] if visual["kind"] == "compare" else [visual]
    return [v["label"] for v in sides if v.get("label")]


# --- Placing the beats (PR 18) ---

def fit_banner(pill, source):
    """A banner's pill, made smaller until it stays inside the safe area at the top of its pop. Returns a problem if
    that takes it under BANNER_MIN_SIZE, where it is too small to read at a glance."""
    while True:
        wide = (pill["size"][0] + 2 * LABEL_BORDER + 2 * LABEL_SHADOW) * LABEL_POP_PEAK   # centred, shadow on one side
        if wide <= SAFE_WIDTH:
            return []
        pill["font"] = min(pill["font"] - 2, int(pill["font"] * SAFE_WIDTH / wide))
        if pill["font"] < BANNER_MIN_SIZE:
            return [f"{source}: the banner {pill['text']!r} is too wide to read at a glance; use shorter words"]
        measure_labels([pill])


def place_compare(visual):
    """A compare beat's two cards: 16:9, as large as fit one above the other in the safe area with their labels (the top
    card's on its top edge, the bottom card's on its bottom edge), centred across on CENTRE_X. seam_y is the middle of
    the gap between them, where the word captions go while it is up."""
    top, bottom = visual["top"], visual["bottom"]
    problems = [f"{side['source']}: the label {side['label']['text']!r} is too wide to read at its size; shorten it"
                for side in (top, bottom)
                if side.get("label") and (side["label"]["size"][0] + 2 * LABEL_BORDER) * LABEL_POP_PEAK > CARD_BOX]
    if problems:
        return problems
    above = label_reach(top["label"])[0] * LABEL_POP_PEAK if top.get("label") else 0
    below = max(label_reach(bottom["label"])[1] * LABEL_POP_PEAK if bottom.get("label") else 0,
                CARD_SHADOW_DY + 2 * CARD_SHADOW_BLUR)
    first, last = SAFE_TOP + math.ceil(above), SAFE_BOTTOM - math.ceil(below)
    aw, ah = CARD_ASPECTS[COMPARE_ASPECT]
    ch = int(min((last - first - COMPARE_GAP) / 2 - 2 * CARD_BORDER, (CARD_BOX - 2 * CARD_BORDER) * ah / aw) // 2) * 2
    cw = round(ch * aw / ah / 2) * 2
    ow, oh = cw + 2 * CARD_BORDER, ch + 2 * CARD_BORDER
    ox, oy = CENTRE_X - ow // 2, round((first + last - 2 * oh - COMPARE_GAP) / 2)
    for side, y in ((top, oy), (bottom, oy + oh + COMPARE_GAP)):
        side["frame"] = {"outer": (ox, y, ow, oh), "content": (ox + CARD_BORDER, y + CARD_BORDER, cw, ch)}
        side["aspect"] = (cw, ch)                    # the card's exact shape, so the crop isn't stretched by a pixel
    if top.get("label"):
        top["label"]["y"] = oy
    if bottom.get("label"):
        bottom["label"]["y"] = oy + 2 * oh + COMPARE_GAP
    visual["seam_y"] = oy + oh + COMPARE_GAP // 2
    return []


def lock_rect(lock, zoom):
    """Where the locked region is on screen at a zoom: left, top, width, height. The camera zooms about a fixed point
    (the anchor), so every point of the picture moves in a straight line away from it as the zoom grows."""
    (x, y, w, h), (ax, ay) = lock["screen"], lock["anchor"]
    return ax + (x - ax) * zoom, ay + (y - ay) * zoom, w * zoom, h * zoom


def lock_frames(frames):
    """The frame the lock-on box lands on, and the frame the fast part of the zoom ends on."""
    lands = round(LOCK_SNAP_S * FPS)
    return lands, max(min(lands + round(LOCK_ZOOM_S * FPS), frames - 1), lands + 1)


def lock_zoom(lock, n, frames):
    """The camera's zoom on frame n of a lock-on shot: still until the box lands, then quickly in (easing out), then a
    slow creep so it never stops dead. lock_motion() gives ffmpeg the same sum."""
    lands, ends = lock_frames(frames)
    p = min(max((n - lands) / (ends - lands), 0.0), 1.0)
    return 1 + (lock["zoom"] - 1) * (1 - (1 - p) ** 3) + LOCK_CREEP_PER_S / FPS * max(n - ends, 0)


def lock_tag_place(lock, rect):
    """The middle of a lock-on's tag for the box at rect: on the box's left corner, above it or below it, and pushed
    in from the safe area's edges if the box sits near one. So while the zoom is still bringing a high box down, its
    tag waits at the top of the safe area, over the box's edge, and then rides on it."""
    tag = lock["tag"]
    left, top, w, h = rect
    (pw, ph), border = tag["size"], tag["border"]
    x = min(max(left - LOCK_EDGE + border + pw / 2, SAFE_LEFT + border + pw / 2),
            W - SAFE_RIGHT - border - tag["shadow"] - pw / 2)
    y = top - LOCK_EDGE - border - ph / 2 if lock["tag_at"] == "above" else top + h + LOCK_EDGE + border + ph / 2
    return x, min(max(y, SAFE_TOP + border + ph / 2), SAFE_BOTTOM - border - tag["shadow"] - ph / 2)


def place_lock(visual, caption_style, warnings):
    """A lock-on's zoom and the point it zooms about. The region should end in the middle of the safe area's width and
    around LOCK_TARGET_Y, above the captions, as large as LOCK_ROOM allows. The largest zoom up to LOCK_ZOOM_MAX that
    leaves the box, its outline and its tag inside the safe area is taken; a picture's edge can stop the region
    reaching the middle, and then a smaller zoom may still fit. Returns problems."""
    lock, source = visual["lock"], visual["source"]
    bx, by, bw, bh = lock["box"]
    x, y, cw, ch = crop_box(visual)[:4]
    if bx < x or by < y or bx + bw > x + cw or by + bh > y + ch:
        return [f"{source}: the region is {bw:.0f}x{bh:.0f} px, and the full screen shows {cw}x{ch} px of this picture "
                "at a time; pick a smaller region"]
    k = W / cw
    x0, y0, w0, h0 = lock["screen"] = ((bx - x) * k, (by - y) * k, bw * k, bh * k)
    tag = lock["tag"]
    if tag and tag["size"][0] + 2 * tag["border"] + tag["shadow"] > SAFE_WIDTH:
        return [f"{source}: the label {tag['text']!r} is too wide to read at its size; shorten it"]
    tag_room = tag["size"][1] + 2 * tag["border"] if tag else 0
    zoom = min(LOCK_ZOOM_MAX, max(LOCK_ZOOM_MIN, min(LOCK_ROOM[0] / w0, LOCK_ROOM[1] / h0)))
    creep = 4 * LOCK_CREEP_PER_S                     # the slow creep after the zoom, for a shot up to about 5 s

    def fits(z):
        left, top, w, h = lock_rect(lock, z)
        up = LOCK_EDGE + (tag_room if lock["tag_at"] == "above" else 0)
        down = LOCK_EDGE + (tag_room + (tag["shadow"] if tag else 0) if lock["tag_at"] == "below" else 0)
        return (left - LOCK_EDGE >= SAFE_LEFT and left + w + LOCK_EDGE <= W - SAFE_RIGHT
                and top - up >= SAFE_TOP and top + h + down <= SAFE_BOTTOM)

    found = False
    while zoom >= LOCK_ZOOM_MIN - 1e-9 and not found:
        for where in ("above", "below"):
            aim = LOCK_TARGET_Y + (tag_room / 2 if where == "above" else -tag_room / 2)
            ax = min(max(((x0 + w0 / 2) * zoom - CENTRE_X) / (zoom - 1), 0), W)
            ay = min(max(((y0 + h0 / 2) * zoom - aim) / (zoom - 1), 0), H)
            lock.update(anchor=(ax, ay), zoom=zoom, tag_at=where)
            if fits(zoom) and fits(zoom + creep):
                found = True
                break
        else:
            zoom -= 0.02
    if not found:
        return [f"{source}: the region can't be brought inside the safe area (x {SAFE_LEFT}-{W - SAFE_RIGHT}, "
                f"y {SAFE_TOP}-{SAFE_BOTTOM}) by zooming up to {LOCK_ZOOM_MAX:g}x: it is too near the picture's top or "
                "bottom, or too large. Pick a region nearer the middle, or use a card beat with a circle mark"]
    left, top, w, h = lock_rect(lock, 1.0)
    if (left - LOCK_EDGE < SAFE_LEFT or left + w + LOCK_EDGE > W - SAFE_RIGHT or top - LOCK_EDGE < SAFE_TOP
            or top + h + LOCK_EDGE > SAFE_BOTTOM):
        warnings.append(f"{source}: the lock-on box lands partly where Shorts draws its buttons or titles, and the "
                        "zoom then brings it into the safe area")
    left, top, w, h = lock_rect(lock, zoom + creep)
    band = CAPTION_BANDS[caption_style]
    if top + h + LOCK_EDGE > band[0] and top - LOCK_EDGE < band[1]:
        warnings.append(f"{source}: the captions cross the locked region; a region higher up the picture stays clear")
    return []


def wrap_words(n, width, limit):
    """Word ranges (i, j) that set n words on the fewest lines no wider than limit, evened out: of every way to fill
    that many lines, the one whose widest line is narrowest. width(i, j) is the width of words i to j on one line.
    None if a single word is too wide."""
    lines, i = 0, 0
    while i < n:
        j = i + 1
        if width(i, j) > limit:
            return None
        while j < n and width(i, j + 1) <= limit:
            j += 1
        lines, i = lines + 1, j

    @functools.lru_cache(maxsize=None)
    def fill(i, left):
        if left == 0:
            return (0.0, ()) if i == n else (math.inf, ())
        best = (math.inf, ())
        for j in range(i + 1, n - left + 2):
            wide = width(i, j)
            if wide > limit:
                break
            rest = fill(j, left - 1)
            if max(wide, rest[0]) < best[0]:
                best = (max(wide, rest[0]), ((i, j),) + rest[1])
        return best
    return list(fill(0, lines)[1])


def place_snippet(visual, caption_style):
    """A snippet's card: its words at the largest size from SNIPPET_SIZE down that fits them, on centred lines, in a
    white card 900 px wide that sits above the captions; and the marker bars behind its highlighted words. Every width
    is libass's own (measure()): first at one size to choose the lines, then at the real size to be sure. Returns
    problems."""
    words, source, label = visual["words"], visual["source"], visual.get("label")
    if label:
        label["pos"] = "top"
        if (label["size"][0] + 2 * LABEL_BORDER) * LABEL_POP_PEAK > CARD_BOX:
            return [f"{source}: the label {label['text']!r} is too wide to read at its size; shorten it"]
    n, ref = len(words), 100
    ranges = [(i, j) for i in range(n) for j in range(i + 1, min(i + 8, n) + 1)]
    boxes = dict(zip(ranges, measure([(chunky(ref), escape_ass(" ".join(words[i:j]))) for i, j in ranges])))
    inner = CARD_BOX - 2 * SNIPPET_PAD_X
    bottom = CAPTION_BANDS[caption_style][0] - CARD_SHADOW_DY - 2 * CARD_SHADOW_BLUR - 8
    # A label sits on the card's top edge, half of it inside: the first line starts clear of it.
    pad_top = max(SNIPPET_PAD_Y, label_reach(label)[1] + 18) if label else SNIPPET_PAD_Y
    for size in range(SNIPPET_SIZE, SNIPPET_MIN_SIZE - 1, -4):
        def width(i, j):
            box = boxes.get((i, j), False)
            return math.inf if box is False else (box[2] - box[0]) * size / ref if box else 0.0
        lines = wrap_words(n, width, inner * 0.98)
        if not lines or len(lines) * size * SNIPPET_LINE + pad_top + SNIPPET_PAD_Y > bottom - SNIPPET_TOP:
            continue
        real = measure([(chunky(size), escape_ass(" ".join(words[i:j]))) for i, j in lines] + [(chunky(size), "Hg")])
        if all(box and box[2] - box[0] <= inner for box in real):
            break
    else:
        return [f"{source}: the text doesn't fit a snippet card even at its smallest size; use fewer or shorter words"]
    cap = real[-1]
    pitch = size * SNIPPET_LINE
    height = round(len(lines) * pitch + pad_top + SNIPPET_PAD_Y)
    top = round(min(max(SNIPPET_MIDDLE_Y - height / 2, SNIPPET_TOP), bottom - height))
    ys = [top + pad_top + pitch * (k + 0.5) for k in range(len(lines))]
    # A marked phrase may run over a line's end: one bar for each line it is on. Its place inside the line comes from
    # drawing the line with only those words showing.
    parts = []
    for a, b in visual["highlight"]:
        for k, (i, j) in enumerate(lines):
            lo, hi = max(a, i), min(b, j)
            if lo < hi:
                before, after = " ".join(words[i:lo]), " ".join(words[hi:j])
                text = (("{\\alpha&HFF&}" + escape_ass(before + " ") if before else "") + "{\\alpha&H00&}"
                        + escape_ass(" ".join(words[lo:hi])) + ("{\\alpha&HFF&}" + escape_ass(" " + after) if after else ""))
                parts.append((k, lo == a, text))
    pad = size * 0.09
    bars, sweeps, at = [], [], SWEEP_DELAY_S
    for (k, starts_phrase, _), box in zip(parts, measure([(chunky(size), text) for _, _, text in parts])):
        if box is None:
            continue
        left, right = CENTRE_X + box[0] - pad, CENTRE_X + box[2] + pad
        middle = ys[k] - (cap[1] + cap[3]) / 2
        if starts_phrase and bars:
            at += SWEEP_GAP_S
        if starts_phrase:
            sweeps.append(round(at, 3))
        took = max(SWEEP_MIN_S, (right - left) / SWEEP_PX_PER_S)
        bars.append({"box": (left, middle + cap[1] - size * 0.05, right, middle + cap[3] + size * 0.03),
                     "at": at, "seconds": took})
        at += took
    visual["snippet"] = {"size": size, "lines": lines, "ys": ys, "ink_middle": (cap[1] + cap[3]) / 2,
                         "card": (CENTRE_X - CARD_BOX // 2, top, CARD_BOX, height), "bars": bars, "sweeps": sweeps}
    if label:
        label["y"] = top
    return []



def label_clear(label, y, band):
    """Whether a label at y stays inside the safe area and LABEL_GAP away from the caption band."""
    up, down = label_reach(label)
    top, bottom = y - up, y + down
    return top >= SAFE_TOP and bottom <= SAFE_BOTTOM and (bottom + LABEL_GAP <= band[0] or top - LABEL_GAP >= band[1])


def card_frame(visual, label, caption_style, warnings):
    """The framed card: the largest box of its shape in CARD_BOX, centred across on CENTRE_X and down as near the safe
    area's middle as the label lets it. A label that can't clear the captions on its edge goes to the other edge."""
    aw, ah = visual["aspect"]
    ow, oh = (CARD_BOX, CARD_BOX * ah / aw) if aw >= ah else (CARD_BOX * aw / ah, CARD_BOX)
    cw, ch = round((ow - 2 * CARD_BORDER) / 2) * 2, round((oh - 2 * CARD_BORDER) / 2) * 2
    ow, oh = cw + 2 * CARD_BORDER, ch + 2 * CARD_BORDER
    band = CAPTION_BANDS[caption_style]
    lowest, highest = SAFE_TOP + oh / 2, SAFE_BOTTOM - oh / 2         # the card itself stays in the safe area
    middle = min(max(CARD_MIDDLE_Y, lowest), highest)
    if label:
        def fits(pos):
            edge = -oh / 2 if pos == "top" else oh / 2
            places = [middle + d * s for d in range(0, 700, 2) for s in (-1, 1)]
            return next((c for c in places if lowest <= c <= highest and label_clear(label, c + edge, band)), None)
        centre = fits(label["pos"])
        if centre is None:
            other = "top" if label["pos"] == "bottom" else "bottom"
            centre = fits(other)
            if centre is not None:
                warnings.append(f"{visual['source']}: its {label['pos']} label would touch the captions, so it goes on "
                                f"the {other} edge")
                label["pos"] = other
        middle = centre if centre is not None else middle
        label["y"] = round(middle - oh / 2 if label["pos"] == "top" else middle + oh / 2)
    ox, oy = CENTRE_X - ow // 2, round(middle - oh / 2)
    return {"outer": (ox, oy, ow, oh), "content": (ox + CARD_BORDER, oy + CARD_BORDER, cw, ch)}


def place_full_label(visual, label, caption_style, warnings):
    band = CAPTION_BANDS[caption_style]
    if not label_clear(label, FULL_LABEL_Y[label["pos"]], band):
        other = "top" if label["pos"] == "bottom" else "bottom"
        warnings.append(f"{visual['source']}: its {label['pos']} label would touch the captions, so it goes at the "
                        f"{other}")
        label["pos"] = other
    label["y"] = FULL_LABEL_Y[label["pos"]]


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
    return warnings + script.get("notes", [])


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


def whoosh(seed, pitch=1.0):
    """A soft whoosh: noise whose pitch climbs to the cut and falls away after it, quiet at the edges. pitch moves the
    whole sweep up or down: a compare beat's first card gets a low one and its second a higher one."""
    import numpy as np
    t = np.arange(int(WHOOSH_S * RATE)) / RATE
    rise, fall = WHOOSH_PEAK_S, WHOOSH_S - WHOOSH_PEAK_S
    low, top, end = 350 * pitch, 2400 * pitch, 800 * pitch
    centre = lambda s: low * (top / low) ** (s / rise) if s < rise else top * (end / top) ** ((s - rise) / fall)
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


def lock_click(seed):
    """A lock-on click: a dry tick, then two short beeps, the second higher, like a target being acquired."""
    import numpy as np
    t = np.arange(int(LOCK_SFX_S * RATE)) / RATE
    tick = swept_noise(np.random.default_rng(seed), LOCK_SFX_S, lambda s: 3200, 0.7) * np.exp(-t / 0.004)
    tick /= max(np.abs(tick).max(), 1e-9)
    beep = lambda hz, at, decay: np.where(t >= at, np.sin(2 * np.pi * hz * (t - at)) * np.exp(-np.maximum(t - at, 0) / decay), 0)
    sound = 0.7 * tick + 0.8 * beep(1250, 0.0, 0.025) + beep(1870, 0.075, 0.045)
    return finish(sound * fade(len(t), 0.03)[::-1], LOCK_DB)


def mouse_click(seed):
    """A mouse click: the button going down (a tick with a short, round body under it, so it carries over the music)
    and, softer and higher, coming back up 80 ms later."""
    import numpy as np
    t = np.arange(int(CLICK_SFX_S * RATE)) / RATE
    rng = np.random.default_rng(seed)

    def press(at, hz, body):
        late = np.maximum(t - at, 0)
        tick = swept_noise(rng, CLICK_SFX_S, lambda s: hz, 0.8) * np.where(t >= at, np.exp(-late / 0.004), 0)
        tick /= max(np.abs(tick).max(), 1e-9)
        return 0.7 * tick + np.where(t >= at, np.sin(2 * np.pi * body * late) * np.exp(-late / 0.03), 0)
    sound = press(0.0, 2400, 880) + 0.6 * press(0.08, 3100, 1320)
    return finish(sound * fade(len(t), 0.03)[::-1], CLICK_DB)


def finish(sound, peak_db):
    """Cut above SFX_LOWPASS_HZ, then set the loudest sample to peak_db."""
    import numpy as np
    sound = lowpass(sound, SFX_LOWPASS_HZ)
    return (sound / max(np.abs(sound).max(), 1e-9) * 10 ** (peak_db / 20)).astype(np.float32)


def plan_sfx(slots, pill_click_s=None):
    """When each sound effect plays and why: a whoosh when a line starts or a pan begins, a hit when a card, a snippet
    or a banner appears. A hit wins over a whoosh at the same moment. Nothing on the first frame, which is also where
    the loop starts again, and nothing in the loop ending but the subscribe pill's click (pill_click_s).
    The beats add their own (PR 18): a compare gets a low whoosh for its first card and a higher one as its second
    lands, and a lock-on gets a click as its box lands. Those clicks never push another sound out."""
    events, started = [], set()
    for slot in slots:
        if slot.get("loop_tail"):
            continue
        at, end, visual, line = slot["start"] / FPS, slot["end"] / FPS, slot["visual"], slot["line"] + 1
        if visual["kind"] in ("card", "snippet") or visual.get("banner"):
            events.append({"kind": "hit", "at_s": at, "why": f"line {line}: {visual['source']} appears"})
        elif visual["kind"] == "compare":
            events.append({"kind": "whoosh", "pitch": "low", "at_s": at, "why": f"line {line}: a compare's first card"})
            second = at + COMPARE_SECOND_S + COMPARE_SLIDE_S / 2
            if second < end - 0.1:
                events.append({"kind": "whoosh", "pitch": "high", "at_s": second,
                               "why": f"line {line}: a compare's second card slides in"})
        elif slot["line"] not in started:
            events.append({"kind": "whoosh", "at_s": at, "why": f"line {line} starts"})
        elif visual.get("pan"):
            events.append({"kind": "whoosh", "at_s": at, "why": f"line {line}: a pan begins"})
        if visual.get("lock") and at + LOCK_SNAP_S < end - 0.1:
            events.append({"kind": "lock", "at_s": at + LOCK_SNAP_S, "why": f"line {line}: the lock-on box lands"})
        started.add(slot["line"])
    if pill_click_s is not None:
        events.append({"kind": "click", "at_s": pill_click_s, "why": "the cursor clicks the subscribe pill"})
    events = [e for e in events if e["at_s"] >= SFX_EARLIEST_S]
    kept = []
    for e in events:  # in time order already: a whoosh too near a hit, or near another whoosh, goes
        if e["kind"] in ("lock", "click"):
            kept.append(e)
            continue
        near = [k for k in kept if k["kind"] in ("whoosh", "hit") and e["at_s"] - k["at_s"] < SFX_SPACING_S]
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
    makers = {"hit": hit, "lock": lock_click, "click": mouse_click}
    for i, e in enumerate(events):
        sound = makers[e["kind"]](i) if e["kind"] in makers else whoosh(i, WHOOSH_PITCH.get(e.get("pitch"), 1.0))
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


def caption_text(words, shown_from=None):
    """A chunk's text. Each untagged word turns HIGHLIGHT while it is being said and back to white when the next word
    starts; the last stays lit until the chunk goes. It is one event with timed colour changes (\\t, in ms from the
    event's start as ass_time rounds it), so the chunk pops in once and the highlight never jumps. shown_from is when
    the event starts, if not with its first word (when it comes back after a text card)."""
    inside_a_tag = {i for i in range(1, len(words)) if words[i]["tag"] is not None and words[i]["tag"] == words[i - 1]["tag"]}
    break_at, size = fit_lines([w["text"] for w in words], CAPTION_CHARS, CAPTION_SIZE, inside_a_tag)
    shown_from = round((words[0]["start"] if shown_from is None else shown_from) * 100) / 100
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


# --- Measuring text with libass itself ---

MEASURE_W, MEASURE_H = 3200, 1400
_INK = {}


def measure(items):
    """Where libass inks each (tags, text), drawn with \\an5 at a point and no outline or shadow: (x1, y1, x2, y2) from
    that point in pixels, the far edges just outside the ink, or None if nothing shows. Everything not measured before
    is measured in one ffmpeg run, one frame each. A piece set to \\alpha&HFF& takes its place but inks nothing, which
    is how one word's place inside a line is found."""
    todo = [item for item in dict.fromkeys(items) if item not in _INK]
    if todo:
        cx, cy = MEASURE_W // 2, MEASURE_H // 2
        with tempfile.TemporaryDirectory(prefix="measure_") as folder:
            for font in FONT_FILES:
                shutil.copy(font, folder)
            events = [dialogue(0, n, n + 1, "Caption", f"{{\\an5\\pos({cx},{cy})\\bord0\\shad0\\blur0\\c{WHITE}{tags}}}{text}")
                      for n, (tags, text) in enumerate(todo)]
            doc = ass_document(events).replace(f"PlayResX: {W}\n", f"PlayResX: {MEASURE_W}\n")
            Path(folder, "m.ass").write_text(doc.replace(f"PlayResY: {H}\n", f"PlayResY: {MEASURE_H}\n"), encoding="utf-8")
            err = ffmpeg(["-f", "lavfi", "-i", f"color=black:s={MEASURE_W}x{MEASURE_H}:r=1:d={len(todo)}", "-vf",
                          "ass=m.ass:fontsdir=.,bbox=min_val=60", "-f", "null", "-"], cwd=folder, loglevel="info")
        found = {int(m.group(1)): tuple(map(int, m.groups()[1:]))
                 for m in re.finditer(r"n:(\d+) pts:\S+ pts_time:\S+ x1:(\d+) x2:(\d+) y1:(\d+) y2:(\d+)", err)}
        for n, item in enumerate(todo):
            box = found.get(n)
            _INK[item] = None if box is None else (box[0] - cx, box[2] - cy, box[1] + 1 - cx, box[3] + 1 - cy)
    return [_INK[item] for item in items]


def centred_width(box):
    """How wide the ink is when what matters is the far side from the anchor: twice its larger half."""
    return 2 * max(-box[0], box[2]) if box else 0


def chunky(size):
    return f"\\fn{CHUNKY}\\fs{size}"


# --- One word at a time ---

def visible(start, end, hidden):
    """The parts of start-end outside every hidden (start, end) span, each at least a frame long."""
    parts = [(start, end)]
    for h0, h1 in hidden:
        kept = []
        for a, b in parts:
            if b <= h0 or a >= h1:
                kept.append((a, b))
                continue
            if a < h0:
                kept.append((a, h0))
            if b > h1:
                kept.append((h1, b))
        parts = kept
    return [(a, b) for a, b in parts if b - a >= 1 / FPS]


def word_events(words, total_s, hidden=(), places=()):
    """The "word" captions: each word alone in Lilita One, yellow (a tagged word keeps its cyan or red) with a thick
    black outline, popping in when it is said and staying until the next word starts, or a moment after it ends when
    a pause follows. Its capitals are centred on WORD_Y, and a word too wide for the safe area is drawn smaller. None
    shows while a text card is up (hidden). places holds (start, end, y): a word that starts in one of those spans is
    centred on that y instead (the seam of a compare beat)."""
    boxes = measure([(chunky(WORD_SIZE), escape_ass(w["text"])) for w in words] + [(chunky(WORD_SIZE), "HH")])
    caps = boxes[-1]
    limit = SAFE_WIDTH / WORD_POP_PEAK - 2 * (WORD_OUTLINE + WORD_SHADOW)   # still in at the top of the pop
    events = []
    for i, (w, box) in enumerate(zip(words, boxes)):
        following = words[i + 1] if i + 1 < len(words) else None
        end = total_s if following is None else following["start"]
        if following is None or w["pause"] or following["sentence"] != w["sentence"]:
            end = min(end, w["end"] + CAPTION_LINGER_S)
        width, size = centred_width(box), WORD_SIZE
        while width > limit:   # measured again, since a very long word can overflow measure()'s canvas
            size = min(size - 1, int(size * limit / width))
            width = centred_width(measure([(chunky(size), escape_ass(w["text"]))])[0])
        line_y = next((py for a, b, py in places if a <= w["start"] < b), WORD_Y)
        y = line_y - (caps[1] + caps[3]) / 2 * size / WORD_SIZE
        colour = COLOURS[w["colour"]] if w["colour"] else HIGHLIGHT
        text = (f"{{\\an5\\pos({CENTRE_X},{y:.0f}){chunky(size)}\\c{colour}\\3c{BLACK}\\bord{WORD_OUTLINE}"
                f"\\shad{WORD_SHADOW}\\4c{BLACK}\\4a&H60&{WORD_POP}}}{escape_ass(w['text'])}")
        events += [dialogue(2, a, b, "Caption", text) for a, b in visible(w["start"], end, hidden)]
    return events


# --- Text cards: number and statement cards, and the open card ---

COUNT_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)(?=\s|$)")


def title_lines(text):
    """A title's lines as (text, style) pieces, trimmed at both ends of each line."""
    tidy = []
    for line in card_lines(text):
        line = list(line)
        line[0] = (line[0][0].lstrip(), line[0][1])
        line[-1] = (line[-1][0].rstrip(), line[-1][1])
        tidy.append([(t, s) for t, s in line if t])
    return tidy


def split_number(lines):
    """The lines with a whole number at the very start of the first line (212, or 1,200) split off as a piece of its
    own, styled "number" unless it is cyan already; and that number's text, or None."""
    text, style = lines[0][0]
    m = COUNT_RE.match(text)
    if not m or style == "strike":
        return lines, None
    rest = text[m.end():]
    first = [(m.group(1), "number" if style is None else style)] + ([(rest, style)] if rest else [])
    return [first + lines[0][1:]] + lines[1:], m.group(1)


def title_markup(lines, show=None, hide=None):
    """ASS text for a title's lines: white or cyan ("green") with a black outline, and the counted number pink with a
    white one, like a sticker. show draws only that (line, piece) and hide leaves that one out; both keep every
    piece's place. A colour block is written only where the colours change."""
    paint = {None: (WHITE, BLACK), "strike": (WHITE, BLACK), "green": (COLOURS["green"], BLACK),
             "number": (ass_colour(PINK), WHITE)}
    out, last = [], None
    for i, line in enumerate(lines):
        parts = []
        for j, (text, style) in enumerate(line):
            clear = (show is not None and (i, j) != show) or (i, j) == hide
            look = (*paint[style], clear)
            if look != last:
                alpha = "\\alpha&HFF&" if clear else "\\1a&H00&\\3a&H00&\\4a&H40&"
                parts.append(f"{{\\c{look[0]}\\3c{look[1]}{alpha}}}")
                last = look
            parts.append(escape_ass(text))
        out.append("".join(parts))
    return "\\N".join(out)


def title_outline(size):
    return max(4, round(size * TITLE_OUTLINE_SHARE)), max(3, round(size * TITLE_SHADOW_SHARE))


def count_values(number):
    """What a counting number shows: a value a frame for COUNT_S, from 0 and easing out, then the number itself, written
    with commas if it has them."""
    final = int(number.replace(",", ""))
    steps = round(COUNT_S * FPS)
    values = [round(final * (1 - (1 - k / steps) ** 3)) for k in range(steps)] + [final]
    return [f"{v:,}" if "," in number else str(v) for v in values]


def fit_title(text, size, room, peak=1.0, counted=False):
    """The title's lines and the size it is drawn at. Plain text too wide for one line breaks in two where the halves
    are most even; then the size comes down until the title, outline and shadow in, fits the safe area's width and
    room pixels of height at its biggest moment: grown to peak by its pop and, if counted, with the widest value its
    number shows while counting ("204" is wider than "212", and reaches further left, since the count keeps the
    number's right edge still). The size is measured again until it really fits, because very wide text can overflow
    measure()'s canvas and measure smaller than it is."""
    lines = title_lines(text)
    if len(lines) == 1 and len(lines[0]) == 1 and lines[0][0][1] is None:
        words = lines[0][0][0].split()
        outline, shadow = title_outline(size)
        if len(words) > 1 and centred_width(measure([(chunky(size), escape_ass(" ".join(words)))])[0]) > \
                SAFE_WIDTH - 2 * (outline + shadow):
            halves = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
            widths = measure([(chunky(size), escape_ass(h)) for pair in halves for h in pair])
            best = min(range(len(halves)), key=lambda k: max(centred_width(widths[2 * k]), centred_width(widths[2 * k + 1])))
            lines = [[(halves[best][0], None)], [(halves[best][1], None)]]
    lines, number = split_number(lines)
    counted = counted and number is not None
    while True:
        outline, shadow = title_outline(size)
        items = [(chunky(size), title_markup(lines))]
        if counted:
            style = lines[0][0][1]
            items += [(chunky(size), title_markup(lines, show=(0, 0)))]
            items += [(chunky(size), title_markup([[(value, style)]])) for value in count_values(number)]
        box, *count_boxes = measure(items)
        edge = outline + shadow
        left, right = (-box[0] + edge) * peak, (box[2] + edge) * peak
        if counted:   # the number doesn't pop, and each value ends where the number does
            place = count_boxes[0]
            left = max(left, max(b[2] - b[0] for b in count_boxes[1:] if b) - place[2] + edge)
        scale = min(SAFE_WIDTH / 2 / max(left, right, 1), room / max((box[3] - box[1] + 2 * outline + shadow) * peak, 1))
        if scale >= 1 or size <= 1:
            return lines, number, size, box
        size = max(1, min(size - 1, int(size * scale)))


def title_events(layer, start, end, text, y, pop, size=CARD_TEXT_SIZE, room=None, count=False, sized_to_count=None):
    """A title in Lilita One, its ink centred on (CENTRE_X, y) and fitted to room pixels of height (by default as much
    as the safe area allows around y), and the bars that cross out its ~marked~ pieces. With count, a number at its
    start counts up from 0 over COUNT_S, right-aligned where it ends, so the words after it never move; it doesn't
    pop, the rest does. Everything has a \\pos, so nothing gets pushed aside, and the bars come last, on top.
    The size leaves room for the pop and, with count, for the widest value counted; sized_to_count (count unless set)
    sizes it that way without counting, so a card that doesn't count this time keeps the size it has when it does."""
    room = room or 2 * min(y - SAFE_TOP - 10, SAFE_BOTTOM - 10 - y)
    lines, number, size, box = fit_title(text, size, room, POP_PEAK if pop else 1.0,
                                         count if sized_to_count is None else sized_to_count)
    outline, shadow = title_outline(size)
    y = y - (box[1] + box[3]) / 2                  # the anchor that centres the ink on y
    base = f"\\an5\\pos({CENTRE_X},{y:.0f}){chunky(size)}\\bord{outline}\\shad{shadow}\\4c{BLACK}\\4a&H40&"
    fade = "\\fad(0,120)" if "\\fad" in pop else ""
    counting = count and number is not None
    events = [dialogue(layer, start, end, "Title", f"{{{base}{pop}}}" + title_markup(lines, hide=(0, 0) if counting else None))]
    if counting:
        events += count_events(layer, start, end, lines, number, size, base, y, fade)
    struck = [(i, j) for i, line in enumerate(lines) for j, (_, style) in enumerate(line) if style == "strike"]
    for (i, j), piece in zip(struck, measure([(chunky(size), title_markup(lines, show=p)) for p in struck])):
        if piece is None:
            continue
        # The bar's outline (and a pixel for its tilt) stays inside the safe area too.
        left = max(CENTRE_X + piece[0] - BAR_PAD * size, SAFE_LEFT + BAR_OUTLINE + 1)
        right = min(CENTRE_X + piece[2] + BAR_PAD * size, W - SAFE_RIGHT - BAR_OUTLINE - 1)
        middle = y + (piece[1] + piece[3]) / 2
        width, height = right - left, BAR_HEIGHT * size
        events.append(dialogue(layer, start, end, "Title",
                               f"{{\\an4\\pos({left:.0f},{middle:.0f})\\org({left + width / 2:.0f},{middle:.0f})"
                               f"\\frz{BAR_TILT}\\bord{BAR_OUTLINE}\\shad0\\c{COLOURS['red']}\\3c{BLACK}\\fscx0"
                               f"\\t({BAR_DELAY_MS},{BAR_DELAY_MS + BAR_GROW_MS},\\fscx100){fade}\\p1}}"
                               f"m 0 0 l {width:.0f} 0 {width:.0f} {height:.0f} 0 {height:.0f}"))
    return events


def count_events(layer, start, end, lines, number, size, base, y, fade):
    """The number at the start of a title counting up from 0, easing out, one value a frame for COUNT_S, then held.
    Each value is placed so its right edge is where the final number's is."""
    shown = count_values(number)
    style = lines[0][0][1]
    one = [[(text, style)] for text in shown]
    boxes = measure([(chunky(size), title_markup([line])) for line in one] + [(chunky(size), title_markup(lines, show=(0, 0)))])
    place = boxes[-1]
    events = []
    for k, (text, box) in enumerate(zip(shown, boxes)):
        a = start + k / FPS
        b = end if k == len(shown) - 1 else min(start + (k + 1) / FPS, end)
        if a >= end or box is None:
            continue
        x = CENTRE_X + place[2] - box[2]
        yy = y + (place[1] + place[3]) / 2 - (box[1] + box[3]) / 2
        tags = base.replace(f"\\pos({CENTRE_X},{y:.0f})", f"\\pos({x:.0f},{yy:.0f})")
        events.append(dialogue(layer, a, b, "Title", f"{{{tags}{fade if k == len(shown) - 1 else ''}}}"
                                                     + title_markup([[(text, style)]])))
    return events


# --- Labels and marks ---

def measure_labels(labels):
    """Each label's pill size and how far its text moves so its ink sits in the pill's middle. Every pill is as tall as
    a capital plus its padding, whatever its letters."""
    if not labels:
        return
    fonts = sorted({label.get("font", LABEL_SIZE) for label in labels})
    boxes = measure([(chunky(label.get("font", LABEL_SIZE)), escape_ass(label["text"])) for label in labels]
                    + [(chunky(font), "HH") for font in fonts])
    capitals = dict(zip(fonts, boxes[len(labels):]))
    for label, box in zip(labels, boxes):
        font = label.get("font", LABEL_SIZE)         # a beat's pill may be smaller or larger, with padding to match
        caps = capitals[font]
        pad_x, pad_y = label.get("pad") or (LABEL_PAD_X * font / LABEL_SIZE, LABEL_PAD_Y * font / LABEL_SIZE)
        x1, _, x2, _ = box or (0, 0, 0, 0)
        label["ink"] = (-(x1 + x2) / 2, -(caps[1] + caps[3]) / 2)
        label["size"] = (x2 - x1 + 2 * pad_x, caps[3] - caps[1] + 2 * pad_y)


def rounded_box(w, h, r):
    """An ASS drawing of a w x h box with corners rounded to r, from (0, 0)."""
    k = 0.5523 * r
    return (f"m {r:.1f} 0 l {w - r:.1f} 0 b {w - r + k:.1f} 0 {w:.1f} {r - k:.1f} {w:.1f} {r:.1f} "
            f"l {w:.1f} {h - r:.1f} b {w:.1f} {h - r + k:.1f} {w - r + k:.1f} {h:.1f} {w - r:.1f} {h:.1f} "
            f"l {r:.1f} {h:.1f} b {r - k:.1f} {h:.1f} 0 {h - r + k:.1f} 0 {h - r:.1f} "
            f"l 0 {r:.1f} b 0 {r - k:.1f} {r - k:.1f} 0 {r:.1f} 0")


def label_events(label, start, end, pop=True, at=None, layers=(3, 4), anim=None):
    """A pill: the label's colour with a white border and a hard shadow, white Lilita One text with a thin black
    outline. Shape and text are both centred on their point, so the pop grows them together. at puts it somewhere
    other than its own place, and anim is its animation if not the pop; a beat's pill may carry its own font size,
    border, shadow, colours and roundness."""
    pw, ph = label["size"]
    dx, dy = label["ink"]
    x, y = at or (label.get("x", CENTRE_X), label["y"])
    anim = (LABEL_POP if pop else "") if anim is None else anim
    colour = ass_colour(label.get("fill") or LABEL_STYLES[label["style"]])
    ink = ass_colour(label["ink_colour"]) if label.get("ink_colour") else WHITE
    shape = (f"{{\\an5\\pos({px(x)},{px(y)})\\bord{label.get('border', LABEL_BORDER)}\\3c{WHITE}"
             f"\\shad{label.get('shadow', LABEL_SHADOW)}\\4c{BLACK}\\4a&H50&"
             f"\\c{colour}{anim}\\p1}}{rounded_box(pw, ph, ph * label.get('round', 0.3))}")
    text = (f"{{\\an5\\pos({x + dx:.1f},{y + dy:.1f}){chunky(label.get('font', LABEL_SIZE))}\\c{ink}\\3c{BLACK}"
            f"\\bord{label.get('text_border', 3)}\\shad0{anim}}}{escape_ass(label['text'])}")
    return [dialogue(layers[0], start, end, "Title", shape), dialogue(layers[1], start, end, "Title", text)]


def px(value):
    """A screen position as an .ass file writes it: a whole number where it is one."""
    return f"{round(value, 1):g}"


def mark_drawing(icon):
    """An icon as polygons around (0, 0), the spot it marks: (fill or None, border colour, border px, points) each."""
    r = MARK_SIZE / 2
    circle = lambda radius, n: [(radius * math.cos(2 * math.pi * i / n), radius * math.sin(2 * math.pi * i / n))
                                for i in range(n)]
    if icon == "x":       # a red disc with a white ring and a white cross of two bars, turned 45 degrees
        arm, half = r * 0.55, r * 0.14
        cross = [(-half, -arm), (half, -arm), (half, -half), (arm, -half), (arm, half), (half, half), (half, arm),
                 (-half, arm), (-half, half), (-arm, half), (-arm, -half), (-half, -half)]
        c = math.cos(math.pi / 4)
        return [(COLOURS["red"], WHITE, 6, circle(r, 48)), (WHITE, WHITE, 0, [((x - y) * c, (x + y) * c) for x, y in cross])]
    if icon == "check":   # a cyan disc with a white ring and a white tick
        tick = [(-0.5 * r, 0.0), (-0.3 * r, -0.2 * r), (-0.12 * r, 0.0), (0.38 * r, -0.48 * r), (0.56 * r, -0.28 * r),
                (-0.12 * r, 0.38 * r)]
        return [(COLOURS["green"], WHITE, 6, circle(r, 48)), (WHITE, WHITE, 0, tick)]
    if icon == "circle":  # a red ring with a white edge around the spot
        ring = circle(r * 1.5, 64)
        return [(None, WHITE, 16, ring), (None, COLOURS["red"], 9, ring)]
    w, h = r * 0.9, r * 1.8   # an arrow pointing down, its tip on the spot
    return [(COLOURS["red"], WHITE, 6, [(0, 0), (-w, -w), (-w * 0.4, -w), (-w * 0.4, -h), (w * 0.4, -h), (w * 0.4, -w),
                                        (w, -w)])]


def mark_events(marks, start, end):
    """Each mark pops in at its time and stays to the end of the shot. libass centres a drawing on \\an5's point the way
    it does the pills, from (0, 0) to its far corner, so each polygon is moved to start at (0, 0) and placed by the
    middle of its box."""
    events = []
    for mark in marks:
        t = start + mark["at"]
        if t >= end - 0.1:
            continue
        x, y = mark["screen"]
        for fill, edge, border, points in mark_drawing(mark["icon"]):
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            mx, my = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            drawing = "m " + " l ".join(f"{px - min(xs):.1f} {py - min(ys):.1f}" for px, py in points)
            paint = f"\\c{fill}" if fill else "\\1a&HFF&"
            events.append(dialogue(5, t, end, "Title", f"{{\\an5\\pos({x + mx:.1f},{y + my:.1f}){paint}\\3c{edge}"
                                                       f"\\bord{border}\\shad3\\4c{BLACK}\\4a&H70&{LABEL_POP}\\p1}}{drawing}"))
    return events


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


def caption_events(words, total_s, hidden=()):
    """The "phrase" captions: each chunk shows from its first word until the next chunk, or a moment after its last
    word if a pause follows. None shows while a text card is up (hidden); a chunk that outlasts the card comes back."""
    events, groups = [], caption_groups(words)
    for group, following in zip(groups, groups[1:] + [None]):
        end = total_s
        if following:
            end = min(following[0]["start"], group[-1]["end"] + CAPTION_LINGER_S)
        events += [dialogue(2, a, b, "Caption", caption_text(group, None if a == group[0]["start"] else a))
                   for a, b in visible(group[0]["start"], end, hidden)]
    return events


def card_spans(slots, caption_style="word"):
    """When text cards are up, in seconds: no caption shows then, because a card says what the voice says. Phrase
    captions also hide over a compare beat, whose two cards leave them no room."""
    return [(s["start"] / FPS, s["end"] / FPS) for s in slots
            if s["visual"]["kind"] == "card" or (caption_style == "phrase" and s["visual"]["kind"] == "compare")]


def caption_places(slots):
    """Where the word captions move to and when: the seam between a compare beat's two cards, while both are up."""
    return [(s["start"] / FPS, s["end"] / FPS, s["visual"]["seam_y"]) for s in slots
            if s["visual"]["kind"] == "compare" and not s.get("loop_tail")]


def card_room(label):
    """Where a text card's lines may go, top to bottom: the safe area less its label, if it has one."""
    top, bottom = SAFE_TOP + 10, SAFE_BOTTOM - 10
    if label:
        up, down = label_reach(label)
        if label["pos"] == "top":
            top = label["y"] + down + LABEL_GAP
        else:
            bottom = label["y"] - up - LABEL_GAP
    return top, bottom


def open_card_events(text, start=0.0, end=OPEN_CARD_S):
    """The open card on layer 1, in the band above OPEN_CARD_Y's mirror, well clear of the captions."""
    return title_events(1, start, end, text, OPEN_CARD_Y, OPEN_POP, OPEN_TEXT_SIZE, 2 * (OPEN_CARD_Y - SAFE_TOP - 10))


def card_events(slots, open_card):
    """Cards on layer 0, the open card on 1, both under the captions on 2. A card's leading number counts up, except in
    the loop ending, which repeats the opening as it ends."""
    events = open_card_events(open_card) if open_card else []
    for slot in slots:
        visual = slot["visual"]
        if visual["kind"] == "card":
            top, bottom = card_room(visual.get("label"))
            events += title_events(0, frame_time(slot["start"]), frame_time(slot["end"]), visual["text"], (top + bottom) / 2, POP,
                                   CARD_TEXT_SIZE, bottom - top, count=not slot.get("loop_tail"), sized_to_count=True)
    return events


def overlay_events(slots, open_card):
    """Labels and marks. A label pops in LABEL_DELAY_S after its shot starts, while the card has finished growing, or
    when the open card goes if it is still up. The first shot's label is there from the first frame (unless the open
    card is up) and stays through the loop ending, so the last frame matches the first."""
    events = []
    open_end = OPEN_CARD_S if open_card else 0.0
    for i, slot in enumerate(slots):
        # A shot's drawings stop on its last frame: a time rounded to the .ass file's hundredths could leave a label
        # up for one frame of the next shot (frame_time).
        visual, start, end = slot["visual"], slot["start"] / FPS, frame_time(slot["end"])
        tail = slot.get("loop_tail")
        label = visual["top"].get("label") if visual["kind"] == "compare" else visual.get("label")
        if label:
            if tail or (i == 0 and not open_card):
                events += label_events(label, frame_time(slot["start"]), end, pop=False)
            else:
                at = max(start + LABEL_DELAY_S, open_end)
                if at < end - 0.2:
                    events += label_events(label, at, end)
        if visual.get("marks") and not tail:
            events += mark_events(visual["marks"], start, end)
        # The beats' own drawings (PR 18). The loop ending repeats the opening's first frame, before any of them.
        if visual["kind"] == "snippet":
            events += snippet_events(slot, settled=i == 0)
        if visual["kind"] == "compare" and not tail and visual["bottom"].get("label"):
            at = start + COMPARE_SECOND_S + COMPARE_SLIDE_S
            if at < end - 0.2:
                events += label_events(visual["bottom"]["label"], at, end)
        if visual.get("lock") and not tail:
            events += lock_events(slot)
        if visual.get("banner") and not tail:
            events += banner_events(slot)
    return events


# --- The beats' drawings (PR 18) ---

def frame_time(frame):
    """A time for the .ass file at which an event starts on exactly this frame of the Short. libass is given each
    frame's time in whole milliseconds (33, 66, 100...) and an .ass time has only hundredths of a second, so a time
    rounded the usual way can land one frame late. This one is at most 12 ms before the frame and never after it."""
    return max(0, math.floor((frame * 1000 / FPS - 1.5) / 10)) / 100


def lock_events(slot):
    """A lock-on's red box and tag, drawn a frame at a time so they stay on the region while the camera zooms. The
    box drops in over LOCK_SNAP_FRAMES frames, from LOCK_SNAP_FROM times its size and faint, and lands LOCK_SNAP_S
    after the cut; the tag appears as it lands. The box is an outline: a white edge under a red line."""
    lock, frames, first = slot["visual"]["lock"], slot["frames"], slot["start"]
    lands = lock_frames(frames)[0]
    events = []
    for n in range(max(lands - LOCK_SNAP_FRAMES, 0), frames):
        left, top, w, h = rect = lock_rect(lock, lock_zoom(lock, n, frames))
        early = min(max((lands - n) / LOCK_SNAP_FRAMES, 0.0), 1.0)      # 1 as the box starts to drop, 0 once landed
        grown = 1 + (LOCK_SNAP_FROM - 1) * early ** 2
        gw, gh = w * grown, h * grown
        a, b = frame_time(first + n), frame_time(first + n + 1)
        place = f"\\an7\\pos({left - (gw - w) / 2:.1f},{top - (gh - h) / 2:.1f})\\1a&HFF&\\shad0"
        faint = f"\\3a&H{round(210 * early):02X}&"
        shape = rounded_box(gw, gh, LOCK_CORNER)
        events.append(dialogue(5, a, b, "Title", f"{{{place}\\3c{WHITE}\\bord{LOCK_EDGE}{faint}\\p1}}{shape}"))
        events.append(dialogue(6, a, b, "Title", f"{{{place}\\3c{COLOURS['red']}\\bord{LOCK_STROKE}{faint}\\p1}}{shape}"))
        if lock["tag"] and n >= lands:
            events += label_events(lock["tag"], a, b, pop=False, at=lock_tag_place(lock, rect), layers=(7, 8))
    return events


def snippet_events(slot, settled=False):
    """A snippet's white card (on layer 0, with the framed card's hard shadow), its marker bars (layer 1) and its lines
    of dark text (layer 3, so the caption layer stays the captions' own). Each bar grows from its left end, one after
    another. The card fades in quickly, except on the Short's first shot and in the loop ending, which also has no
    bars: the last frame is the first frame, before any sweep."""
    visual = slot["visual"]
    s, words = visual["snippet"], visual["words"]
    start, end = frame_time(slot["start"]), frame_time(slot["end"])
    tail = slot.get("loop_tail")
    fade = "" if settled or tail else "\\fad(100,0)"
    x, y, w, h = s["card"]
    events = [dialogue(0, start, end, "Title",
                       f"{{\\an7\\pos({x},{y})\\bord0\\xshad{CARD_SHADOW_DX}\\yshad{CARD_SHADOW_DY}\\c{WHITE}\\4c{BLACK}"
                       f"\\4a&H38&{fade}\\p1}}{rounded_box(w, h, SNIPPET_RADIUS)}")]
    for bar in ([] if tail else s["bars"]):
        left, top, right, bottom = bar["box"]
        a, b = round(bar["at"] * 1000), round((bar["at"] + bar["seconds"]) * 1000)
        events.append(dialogue(1, start, end, "Title",
                               f"{{\\an4\\pos({left:.1f},{(top + bottom) / 2:.1f})\\bord0\\shad0\\c{HIGHLIGHT}\\fscx0"
                               f"\\t({a},{b},\\fscx100)\\p1}}m 0 0 l {right - left:.0f} 0 {right - left:.0f} "
                               f"{bottom - top:.0f} 0 {bottom - top:.0f}"))
    for (i, j), line_y in zip(s["lines"], s["ys"]):
        events.append(dialogue(3, start, end, "Title",
                               f"{{\\an5\\pos({CENTRE_X},{line_y - s['ink_middle']:.1f}){chunky(s['size'])}"
                               f"\\c{ass_colour(INK)}\\bord0\\shad0{fade}}}{escape_ass(' '.join(words[i:j]))}"))
    return events


def banner_events(slot):
    """A banner's pill, popping in just after the cut and up for BANNER_S, or to the end of a shorter shot."""
    pill = slot["visual"]["banner"]
    start, end = slot["start"] / FPS + BANNER_DELAY_S, frame_time(slot["end"])
    if start >= end - 0.2:
        return []
    if end - start > BANNER_S + 0.3:
        return label_events(pill, start, start + BANNER_S, anim=LABEL_POP + "\\fad(0,120)")
    return label_events(pill, start, end)


CURSOR = [(0, 0), (0, 46), (11, 36), (20, 56), (29, 52), (20, 33), (35, 33)]   # an arrow pointer, its tip at (0, 0)


def pill_frames(total_frames):
    """The frames the subscribe pill appears on, is clicked on, and has gone by."""
    return tuple(total_frames - round(s * FPS) for s in (PILL_S, PILL_CLICK_S, PILL_OUT_S))


def place_end_pill(pill, slots, total_frames):
    """Put the subscribe pill under the captions, or at the top of the safe area if a label or a card is in its way
    there during its 2 s. Returns a warning if both places are taken."""
    first = pill_frames(total_frames)[0]
    half = pill["size"][1] / 2 * LABEL_POP_PEAK + pill["shadow"] + LABEL_GAP

    def taken(y):
        spans = []
        for slot in slots:
            visual = slot["visual"]
            if slot["end"] <= first:
                continue
            spans += [(label["y"] - label_reach(label)[0], label["y"] + label_reach(label)[1]) for label in labels_of(visual)]
            if visual["kind"] == "snippet":
                spans.append((visual["snippet"]["card"][1], visual["snippet"]["card"][1] + visual["snippet"]["card"][3]))
            if visual["kind"] == "compare":
                spans += [(visual[side]["frame"]["outer"][1], visual[side]["frame"]["outer"][1] + visual[side]["frame"]["outer"][3])
                          for side in ("top", "bottom")]
        return any(lo < y + half and hi > y - half for lo, hi in spans)
    for pos in ("bottom", "top"):
        pill["pos"], pill["y"] = pos, PILL_Y[pos]
        if not taken(PILL_Y[pos]):
            return None
    pill["pos"], pill["y"] = "bottom", PILL_Y["bottom"]
    return ("the subscribe pill covers a label or a card in the last 2 s; end the script on a full-screen shot or a "
            "plain framed card")


def end_pill_events(pill, total_frames):
    """The ending's subscribe pill, on layers above everything: it pops in white with dark text; a cursor slides onto
    it, easing to a stop; on the click the pill turns pink with white text and dips, and a ring spreads from the
    cursor's tip; then pill and cursor fade, PILL_OUT_S before the end. Nothing is said: it is only seen."""
    f_in, f_click, f_out = pill_frames(total_frames)
    a, c, b = frame_time(f_in), frame_time(f_click), frame_time(f_out)
    events = label_events(pill, a, c, layers=(9, 10))
    events += label_events({**pill, "fill": PINK, "ink_colour": None}, c, b, layers=(9, 10), anim=PILL_PRESS + "\\fad(0,130)")
    cx, cy = CENTRE_X + pill["size"][0] * 0.3, pill["y"] - 4
    sx, sy = min(cx + 170, W - SAFE_RIGHT - 50), cy + (-150 if pill["pos"] == "bottom" else 150)
    arrow = "m " + " l ".join(f"{x} {y}" for x, y in CURSOR)
    first, arrives = f_in + 4, f_click - 3
    for f in range(first, f_out):
        e = 1 - (1 - min((f - first) / max(arrives - first, 1), 1.0)) ** 3
        faint = min(max((f - (f_out - 5)) / 4, 0.0), 1.0)
        events.append(dialogue(11, frame_time(f), frame_time(f + 1), "Title",
                               f"{{\\an7\\pos({sx + (cx - sx) * e:.1f},{sy + (cy - sy) * e:.1f})\\c{WHITE}\\3c{BLACK}\\bord4"
                               f"\\shad3\\4c{BLACK}\\4a&H70&\\alpha&H{round(255 * faint):02X}&\\p1}}{arrow}"))
    ring = [(30 + 30 * math.cos(2 * math.pi * k / 40), 30 + 30 * math.sin(2 * math.pi * k / 40)) for k in range(40)]
    events.append(dialogue(11, c, frame_time(f_click + 9), "Title",
                           f"{{\\an5\\pos({cx:.1f},{cy:.1f})\\1a&HFF&\\3c{WHITE}\\bord5\\shad0\\fscx40\\fscy40"
                           f"\\t(0,260,\\fscx120\\fscy120)\\fad(0,170)\\p1}}m "
                           + " l ".join(f"{x:.1f} {y:.1f}" for x, y in ring)))
    return events


# --- The timeline ---

def plan_slots(lines, line_starts, total_frames):
    """Every visual's frames, in order. A line's time is shared by its visuals' weights (1 each unless set). Cuts come
    from absolute times, so rounding never adds up to a drift. The loop ending takes the last frames of the voice's own
    time: adding them after it would make the video longer than the voice.

    No visual is dropped for it. A shot the loop ending would cover, or leave with under MIN_SHOWN_FRAMES, starts
    earlier instead, so it gets that much; the shot before it ends there, and if that one is left too short it moves
    up the same way. (Before PR 19's fix a short last line lost its pictures: "Just remember one thing:" lasted 1.3 s,
    the loop ending took 1.5 s, and its picture never appeared.) In a Short too short to give every shot that much,
    they share what there is."""
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
    if slots and tail > slots[0]["start"]:
        first = slots[0]["visual"]
        need = min(MIN_SHOWN_FRAMES, (tail - slots[0]["start"]) // len(slots))
        limit = tail
        for slot in reversed(slots):
            if slot["end"] <= limit:
                break                                   # this shot and every one before it keep their own time
            slot["end"] = limit
            slot["start"] = min(slot["start"], limit - need)
            limit = slot["start"]
        slots = [s for s in slots if s["end"] > s["start"]]
        slots.append({"line": len(lines) - 1, "start": tail, "end": total_frames, "visual": first, "loop_tail": True})
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
    """The part of a picture its shot shows, centred on its focus as far as the picture allows: 9:16 to fill the
    screen, or the framed card's shape. x, y, width, height, and where the subject sits across and down that part
    (0-1)."""
    iw, ih = visual["size"]
    fx, fy = visual["focus"], visual.get("focus_y", 0.5)
    aw, ah = visual.get("aspect", (W, H))
    if iw * ah > ih * aw:
        cw, ch = round(ih * aw / ah), ih
    else:
        cw, ch = iw, round(iw * ah / aw)
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
        if visual.get("lock"):                       # a lock-on's own zoom, on a picture or a video
            slot["motion"] = lock_motion(visual, slot["frames"], bool(slot.get("loop_tail")))
            continue
        if visual["kind"] == "compare":              # each card's picture zooms slowly at its focus
            slot["motions"] = [make_motion("zoom_in", 1.0, slot["frames"], *crop_box(visual[side])[4:])
                               for side in ("top", "bottom")]
            if slot.get("loop_tail") and slots[0].get("motions"):
                slot["motions"][0] = return_motion(slots[0]["motions"][0], slots[0]["frames"], slot["frames"])
            continue
        if visual["kind"] != "image" or slot.get("loop_tail"):
            continue
        if visual.get("beat") in ("full", "banner") and not visual.get("pan") and not visual.get("marks"):
            # A full-screen beat always heads for its focus.
            slot["motion"] = make_motion("zoom_in", 1.0, slot["frames"], *crop_box(visual)[4:])
            last = "zoom_in"
            continue
        if visual.get("pan"):
            slot["motion"] = make_reveal(visual, visual["pan"])
            continue
        if visual.get("marks"):  # marks stay on their spot, so the picture holds still
            subject, middle = crop_box(visual)[4:]
            slot["motion"] = {"kind": "still", "speed": 1.0, "zoom": (1.0, 1.0), "centre": (subject, subject),
                              "middle": middle}
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
    if slots[-1].get("loop_tail") and slots[0]["visual"]["kind"] == "image" and "motion" not in slots[-1]:
        slots[-1]["motion"] = return_motion(slots[0]["motion"], slots[0]["frames"], slots[-1]["frames"])


def lock_motion(visual, frames, tail=False):
    """A lock-on shot's camera, as zoompan's own sums: the zoom lock_zoom() gives, about the anchor, which keeps the
    window inside the picture at any zoom. The loop ending eases back from a slight zoom to none, without the box."""
    lock = visual["lock"]
    ax, ay = lock["anchor"][0] / W, lock["anchor"][1] / H
    if tail:
        zoom, kind = f"1+{LOCK_TAIL_ZOOM}*(1-on/{max(frames - 1, 1)})", "back_to_first_frame"
    else:
        lands, ends = lock_frames(frames)
        zoom = (f"1+{lock['zoom'] - 1:.5f}*(1-pow(1-clip((on-{lands})/{ends - lands},0,1),3))"
                f"+{LOCK_CREEP_PER_S / FPS:.6f}*max(on-{ends},0)")
        kind = "lock_on"
    return {"kind": kind, "speed": 1.0, "expr": (zoom, f"{ax:.5f}*iw*(1-1/zoom)", f"{ay:.5f}*ih*(1-1/zoom)")}


def assign_backgrounds(slots):
    """The blurred background's very slow zoom, and whether the framed card grows in. Every shot's card grows from 96 %
    as it starts, except the first shot's and the loop ending's, which start and end settled so the Short's last frame
    is its first; the loop ending's background zooms back to where the opening's started."""
    for i, slot in enumerate(slots):
        slot["bg_zoom"] = (1.0, 1.0 + BG_ZOOM_PER_S * max(slot["frames"] - 1, 1) / FPS)
        slot["card_in"] = i > 0 and not slot.get("loop_tail")
    if len(slots) > 1 and slots[-1].get("loop_tail"):
        reach = min(slots[-1]["frames"], slots[0]["frames"]) - 1
        slots[-1]["bg_zoom"] = (1.0 + BG_ZOOM_PER_S * max(reach, 1) / FPS, 1.0)


def pan_speed(slot):
    """How fast a reveal moves, in widths of its window a second: the screen's, or the framed card's."""
    visual, motion = slot["visual"], slot["motion"]
    widths = abs(motion["centre"][1] - motion["centre"][0]) * visual["size"][0] / crop_box(visual)[2]
    return widths / (max(slot["frames"] - 1, 1) / FPS)


@functools.lru_cache(maxsize=None)
def seam_psnr(path, frames):
    """How alike a clip's last and first frames are, in dB."""
    p = str(path)
    err = ffmpeg([*source_args(path), "-i", p, *source_args(path), "-i", p, "-filter_complex",
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


def moving_picture(visual):
    """The video whose frames a shot shows or blurs: its own, a text card's bg or a compare's top one. None if it is
    a still."""
    behind = visual.get("bg") if visual["kind"] in ("card", "snippet") else picture_of(visual)
    return behind if behind and behind["kind"] == "video" else None


def bounce_order(clip_frames, frames):
    """Which of a clip's frames the loop ending shows, when the file has nothing before the opening's start: the clip
    run backwards into its first frame, so the Short's last frame is its first and nothing ever stands still (PR 18;
    before, the ending held that first frame for 1.5 s). A clip shorter than the ending plays forwards first and
    turns round at its last frame, as often as it takes."""
    period = 2 * (clip_frames - 1)
    back = lambda d: d % period if d % period < clip_frames else period - d % period
    return [back(frames - 1 - j) for j in range(frames)]


def plan_videos(slots):
    """Each video slot's start time and fill. The loop ending runs up to the point the opening starts from, so the
    join back to the start is a continuous shot and the Short's last frame runs into its first. With less than that
    in the file before the opening's start (a clip used from 0), the ending plays the opening backwards into its
    first frame instead (bounce_order). That goes for a video behind a text card or in a compare's top card too."""
    for slot in slots:
        visual = slot["visual"]
        video = moving_picture(visual)
        if video is None:
            continue
        slot["start_s"] = video["start"]
        if slot.get("loop_tail"):
            clip_frames = min(int((video["length"] - video["start"]) * FPS + 0.5), slot["frames"])
            if round(video["start"] * FPS) >= slot["frames"]:
                slot["start_s"] = video["start"] - slot["frames"] / FPS
            elif clip_frames >= 2:
                slot["bounce"] = bounce_order(clip_frames, slot["frames"])
                if visual["kind"] == "video":
                    slot["fill"] = "bounce"
                continue
        if visual["kind"] == "video":
            slot["fill"] = fill_mode(visual, slot["start_s"], slot["frames"])


def source_graph(slot, source="[0:v]"):
    """The start of a filter graph for a shot's video: nothing, and the input itself, for a shot that just plays. For
    a loop ending that bounces: the clip's frames in bounce_order, as runs played forwards or backwards and joined,
    named [src]. Returns the graph's text and the name to read from."""
    order = slot.get("bounce")
    if not order:
        return "", source
    runs = [[order[0]]]
    for frame in order[1:]:
        run = runs[-1]
        if abs(frame - run[-1]) == 1 and (len(run) == 1 or frame - run[-1] == run[-1] - run[-2]):
            run.append(frame)
        else:
            runs.append([frame])
    graph = f"{source}fps={FPS},format=yuv420p,split={len(runs)}" + "".join(f"[s{i}]" for i in range(len(runs))) + ";"
    for i, run in enumerate(runs):
        back = ",reverse" if run[0] > run[-1] else ""
        graph += f"[s{i}]trim=start_frame={min(run)}:end_frame={max(run) + 1}{back},setpts=N/{FPS}/TB[r{i}];"
    if len(runs) == 1:
        return graph + "[r0]null[src];", "[src]"
    return graph + "".join(f"[r{i}]" for i in range(len(runs))) + f"concat=n={len(runs)}:v=1:a=0[src];", "[src]"


def slot_warnings(slots, lines, last_line_frames):
    warnings = []
    for slot in slots:
        seconds, visual = slot["frames"] / FPS, slot["visual"]
        panning = bool(visual.get("pan")) and not slot.get("loop_tail")
        busy = visual.get("beat") in ("lockon", "compare", "snippet")   # something arrives inside these: see the pacing
        if seconds > (LONG_PAN_S if panning else LONG_SLOT_S) and not slot.get("loop_tail") and not busy:
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

FINISH = ",format=yuv420p,setsar=1"


def picture_size(visual):
    """The size a picture is drawn at: the whole screen, or the inside of its framed card."""
    return tuple(visual["frame"]["content"][2:]) if visual.get("layout") == "framed" else (W, H)


def graded(grade):
    """The colour grade's filters, to come before FINISH, or nothing."""
    return f",{GRADE},{VIGNETTE}" if grade else ""


def image_filter(visual, motion, frames, tail=FINISH):
    """Crop to 9:16 (or the card's shape) around the focus, enlarge, then move a window over it. A reveal pans over the
    whole picture instead. tail ends the chain."""
    if motion.get("wide"):
        return reveal_filter(visual, motion, frames, tail)
    x, y, crop_w, crop_h, _, _ = crop_box(visual)
    ow, oh = picture_size(visual)
    if motion.get("expr"):   # a lock-on: zoompan's zoom, x and y are written out in full (lock_motion)
        zoom, across, down = motion["expr"]
        return (f"crop={crop_w}:{crop_h}:{x}:{y},scale={ow * CANVAS_SCALE}:{oh * CANVAS_SCALE}:flags=lanczos:{COLOUR_OUT},"
                f"format=yuv444p,zoompan=z='{zoom}':x='{across}':y='{down}':d={frames}:s={ow}x{oh}:fps={FPS}{tail}")
    p = f"on/{max(frames - 1, 1)}"
    (z0, z1), (c0, c1), middle = motion["zoom"], motion["centre"], motion.get("middle", 0.5)
    return (f"crop={crop_w}:{crop_h}:{x}:{y},scale={ow * CANVAS_SCALE}:{oh * CANVAS_SCALE}:flags=lanczos:{COLOUR_OUT},"
            f"format=yuv444p,zoompan=z='{z0:.5f}+({z1 - z0:.5f})*{p}':"
            f"x='clip(({c0:.5f}+({c1 - c0:.5f})*{p})*iw-iw/zoom/2,0,iw-iw/zoom)':"
            f"y='clip({middle:.5f}*ih-ih/zoom/2,0,ih-ih/zoom)':d={frames}:s={ow}x{oh}:fps={FPS}{tail}")


def reveal_filter(visual, motion, frames, tail=FINISH):
    """A pan over the whole picture. zoompan can't do it: its window keeps the picture's own shape. So the picture is
    enlarged to 3x the window's height once, that one frame repeats, a window of the screen's (or the card's) shape
    slides across it a whole number of enlarged pixels per frame (a third of a screen pixel, too fine to see as a
    shake), and each frame is scaled down. A pan moves tens of pixels a frame, so a sideways blur the size of a
    camera's half-open shutter keeps it smooth."""
    iw, ih = visual["size"]
    ow, oh = picture_size(visual)
    tall = oh * CANVAS_SCALE
    wide = round(iw * tall / ih / 2) * 2
    window = ow * CANVAS_SCALE
    (c0, c1), steps = motion["centre"], max(frames - 1, 1)
    x = f"clip(({c0:.6f}+({c1 - c0:.6f})*n/{steps})*{wide}-{window // 2},0,{wide - window})"
    blur = PAN_BLUR * abs(c1 - c0) * wide / steps / CANVAS_SCALE
    blur = f",gblur=sigma={blur:.2f}:sigmaV=0:steps=2" if blur >= 0.3 else ""
    return (f"scale={wide}:{tall}:flags=lanczos:{COLOUR_OUT},format=yuv444p,loop=loop=-1:size=1,setpts=N/{FPS}/TB,"
            f"crop={window}:{tall}:x='{x}':y=0,scale={ow}:{oh}:flags=lanczos{blur}{tail}")


def video_filter(focus=0.5, focus_y=0.5, grade=False):
    """Fill the frame, cropping around the focus. tpad holds the last frame if the file runs out."""
    return (f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos:{COLOUR_OUT},"
            f"crop={W}:{H}:x='clip({focus:.4f}*iw-{W // 2},0,iw-{W})':y='clip({focus_y:.4f}*ih-{H // 2},0,ih-{H})',"
            f"setsar=1{graded(grade)},format=yuv420p,tpad=stop=-1:stop_mode=clone")


def lock_video_filter(visual, motion, grade=False):
    """A lock-on over a video: the 9:16 part around the region at twice the screen's size, then the lock-on's zoom a
    frame at a time (zoompan makes one frame from each)."""
    x, y, crop_w, crop_h = crop_box(visual)[:4]
    zoom, across, down = motion["expr"]
    return (f"fps={FPS},crop={crop_w}:{crop_h}:{x}:{y},scale={W * 2}:{H * 2}:flags=bicubic:{COLOUR_OUT},"
            f"zoompan=z='{zoom}':x='{across}':y='{down}':d=1:s={W}x{H}:fps={FPS},"
            f"setsar=1{graded(grade)},format=yuv420p,tpad=stop=-1:stop_mode=clone")


def source_args(path):
    """What goes before a video's -i: clip.py's fix for PS5 captures (VP9 whose header says full range while the
    container, rightly, says limited), or nothing."""
    video = stream_of(probe(path), "video") or {}
    return RANGE_FIX_ARGS if needs_range_fix(video.get("codec_name"), video.get("color_range")) else []


def background_small(visual):
    """The background before its brightness: the picture's 9:16 part around its focus, at BG_SMALL, blurred, with more
    colour. In TV-range YUV, where scaling brightness is exact (see background_chain)."""
    x, y, cw, ch = crop_box({**visual, "aspect": (W, H)})[:4]
    sw, sh = BG_SMALL
    return (f"crop={cw}:{ch}:{x}:{y},scale={sw}:{sh}:flags=area:{COLOUR_OUT},format=yuv444p,"
            f"gblur=sigma={BG_BLUR * sw / W:.2f}:steps=3,eq=saturation={BG_SATURATION}")


@functools.lru_cache(maxsize=None)
def background_gain(visual_key):
    """How much the background is darkened (or, for a very dark picture, lifted) so its average brightness is BG_LEVEL,
    from its first frame."""
    import numpy as np
    path, start, focus, focus_y, size = visual_key
    visual = {"path": path, "focus": focus, "focus_y": focus_y, "size": size}
    seek = ["-ss", f"{start:.3f}"] if start else []
    r = subprocess.run([FFMPEG, "-v", "error", *source_args(path), *seek, "-i", str(path), "-frames:v", "1", "-vf",
                        background_small(visual) + ",scale=in_color_matrix=bt709:in_range=tv,format=rgb24",
                        "-f", "rawvideo", "-"], capture_output=True)
    sw, sh = BG_SMALL
    if r.returncode or len(r.stdout) < sw * sh * 3:
        raise ScriptError(f"ffmpeg couldn't read the first frame of {path}")
    rgb = np.frombuffer(r.stdout[:sw * sh * 3], np.uint8).reshape(sh, sw, 3) / 255.0
    luma = float((rgb @ np.array([0.2126, 0.7152, 0.0722])).mean())
    return min(BG_LEVEL / max(luma, 1e-3), BG_MAX_GAIN)


def background_chain(visual, frames, zoom, start=0.0):
    """The blurred, darkened copy behind a framed card or a text card, at full screen. A still zooms very slowly
    (zoom: from, to); a video plays and holds its last frame if it runs out. Brightness is scaled in TV-range YUV:
    luma from 16 and colour from 128, which is the same as scaling red, green and blue."""
    k = background_gain((visual["path"], start, visual["focus"], visual.get("focus_y", 0.5), tuple(visual["size"])))
    chain = (background_small(visual) +
             f",lutyuv=y='(val-16)*{k:.4f}+16':u='(val-128)*{k:.4f}+128':v='(val-128)*{k:.4f}+128'")
    if visual["kind"] == "image":
        z0, z1 = zoom
        return (chain + f",scale={W * 2}:{H * 2}:flags=bicubic,zoompan=z='{z0:.5f}+({z1 - z0:.5f})*on/{max(frames - 1, 1)}':"
                f"x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s={W}x{H}:fps={FPS}")
    return f"fps={FPS}," + chain + f",scale={W}:{H}:flags=bicubic,tpad=stop=-1:stop_mode=clone"


# --- The framed card's layer, drawn once per size ---

def rounded_rect(w, h, x0, y0, rw, rh, r):
    """How much of each pixel of a w x h canvas a rounded rectangle covers (0-1), with smooth edges."""
    import numpy as np
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32) + 0.5
    qx = np.abs(xs - (x0 + rw / 2)) - (rw / 2 - r)
    qy = np.abs(ys - (y0 + rh / 2)) - (rh / 2 - r)
    d = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r
    return np.clip(0.5 - d, 0, 1)


def soften(a, sigma):
    """A Gaussian blur, in the frequency domain, one direction at a time."""
    import numpy as np
    pad = int(4 * sigma) + 1
    for axis in (0, 1):
        n = a.shape[axis]
        p = np.pad(a, [(pad, pad) if ax == axis else (0, 0) for ax in (0, 1)])
        shape = [1, 1]
        shape[axis] = -1
        g = np.exp(-2 * (np.pi * np.fft.rfftfreq(p.shape[axis]) * sigma) ** 2).reshape(shape)
        a = np.take(np.fft.irfft(np.fft.rfft(p, axis=axis) * g, p.shape[axis], axis=axis), np.arange(pad, pad + n), axis=axis)
    return a


def write_png(path, pixels):
    """An 8-bit grey (2-D) or RGBA (H x W x 4) array as a PNG, with zlib alone."""
    import numpy as np
    h, w = pixels.shape[:2]
    kind = 0 if pixels.ndim == 2 else 6
    rows = np.hstack([np.zeros((h, 1), np.uint8), pixels.reshape(h, -1)]).tobytes()
    chunk = lambda tag, data: (struct.pack(">I", len(data)) + tag + data
                               + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, kind, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


_LAYER_LOCK = threading.Lock()


def card_layer(folder, frame):
    """The framed card's layer and its picture's mask, as PNGs in folder, drawn once per size: a white box with rounded
    corners over its hard shadow, with CARD_MARGIN of room on every side; and the picture's own rounded corners, a
    little tighter, so the white shows as a border all round."""
    import numpy as np
    ow, oh = frame["outer"][2:]
    cw, ch = frame["content"][2:]
    layer, mask = Path(folder) / f"card_{ow}x{oh}.png", Path(folder) / f"mask_{cw}x{ch}.png"
    with _LAYER_LOCK:
        if not layer.is_file():
            lw, lh, m = ow + 2 * CARD_MARGIN, oh + 2 * CARD_MARGIN, CARD_MARGIN
            box = rounded_rect(lw, lh, m, m, ow, oh, CARD_RADIUS)
            shadow = soften(rounded_rect(lw, lh, m + CARD_SHADOW_DX, m + CARD_SHADOW_DY, ow, oh, CARD_RADIUS),
                            CARD_SHADOW_BLUR).clip(0, 1) * CARD_SHADOW_ALPHA
            alpha = box + shadow * (1 - box)
            white = 255 * box / np.maximum(alpha, 1e-6)          # white over black, as straight alpha
            write_png(layer, np.dstack([white, white, white, alpha * 255]).round().clip(0, 255).astype(np.uint8))
        if not mask.is_file():
            corners = rounded_rect(cw, ch, 0, 0, cw, ch, max(CARD_RADIUS - CARD_BORDER, 4))
            write_png(mask, (corners * 255).round().astype(np.uint8))
    return layer, mask


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


def picture_input(visual, slot, start=None):
    """ffmpeg's input arguments for a still or a video. A reveal's repeated frame takes its rate from the input."""
    if visual["kind"] == "image":
        rate = ["-framerate", FPS] if slot.get("motion", {}).get("wide") else []
        return ["-f", "image2", "-pattern_type", "none", *rate, "-i", visual["path"]]
    loop = ["-stream_loop", "-1"] if slot.get("fill") == "loop" else []
    # A bouncing loop ending reads only the frames it turns round on, not the rest of a long file.
    span = ["-t", f"{(max(slot['bounce']) + 3) / FPS:.3f}"] if slot.get("bounce") else []
    return [*loop, *source_args(visual["path"]), "-ss", f"{visual['start'] if start is None else start:.3f}", *span,
            "-i", visual["path"]]


def framed_graph(slot):
    """A framed shot: the blurred background; the picture, graded, through its rounded mask onto the card's layer;
    the card growing in (unless it is the first shot or the loop ending) and laid over the middle of its place."""
    visual, frames = slot["visual"], slot["frames"]
    ox, oy, ow, oh = visual["frame"]["outer"]
    cw, ch = visual["frame"]["content"][2:]
    lw, lh = ow + 2 * CARD_MARGIN, oh + 2 * CARD_MARGIN
    grade = slot.get("grade", False)
    if visual["kind"] == "image":
        source, picture = "[0:v]", image_filter(visual, slot["motion"], frames, tail="")
    else:
        before, name = source_graph(slot)
        source = f"{before}{name}fps={FPS},"
        x, y, w, h = crop_box(visual)[:4]
        picture = (f"crop={w}:{h}:{x}:{y},scale={cw}:{ch}:flags=lanczos:{COLOUR_OUT},setsar=1,"
                   "tpad=stop=-1:stop_mode=clone")
    grow = ""
    if slot.get("card_in"):
        s = f"({CARD_IN_FROM}+{1 - CARD_IN_FROM:.2f}*(1-pow(1-min(t/{CARD_IN_S}\\,1)\\,2)))"
        grow = f",scale=w='trunc({lw}*{s}/2)*2':h='trunc({lh}*{s}/2)*2':eval=frame"
    # The background's brightness is measured where the shot starts, so the loop ending matches the opening.
    background = background_chain(visual, frames, slot.get("bg_zoom", (1.0, 1.0)), visual.get("start", 0.0))
    centre_x, centre_y = ox + ow / 2, oy + oh / 2
    grade_picture = f",{GRADE}" if grade else ""
    vignette = f",{VIGNETTE}" if grade else ""
    return (f"{source}split=2[a][b];[a]{background}[bg];[b]{picture}{grade_picture},format=rgba[pic];"
            f"[2:v]format=gray[m];[pic][m]alphamerge[art];[1:v]format=rgba[frame];"
            f"[frame][art]overlay={CARD_MARGIN + CARD_BORDER}:{CARD_MARGIN + CARD_BORDER}:format=auto{grow}[card];"
            f"[bg][card]overlay=x='{centre_x:.0f}-w/2':y='{centre_y:.0f}-h/2':eval=frame:format=auto{vignette},"
            f"scale={COLOUR_OUT}{FINISH}")


def compare_graph(slot):
    """A compare beat: the top picture's blurred background; each picture, graded, through the rounded mask onto a
    card layer; the top card growing in as a framed card does; and the bottom card sliding up from below the screen,
    COMPARE_SECOND_S after the first, easing to a stop. The loop ending has the top card alone, as the first frame
    does. Inputs: the top picture, the bottom picture, the card layer, the mask."""
    visual, frames = slot["visual"], slot["frames"]
    top, bottom = visual["top"], visual["bottom"]
    ox, oy, ow, oh = top["frame"]["outer"]
    cw, ch = top["frame"]["content"][2:]
    lw, lh = ow + 2 * CARD_MARGIN, oh + 2 * CARD_MARGIN
    grade = slot.get("grade", False)
    motions = slot.get("motions") or [None, None]

    def art(side, motion):
        if side["kind"] == "image":
            chain = image_filter(side, motion, frames, tail="")
        else:
            x, y, w, h = crop_box(side)[:4]
            chain = (f"fps={FPS},crop={w}:{h}:{x}:{y},scale={cw}:{ch}:flags=lanczos:{COLOUR_OUT},setsar=1,"
                     "tpad=stop=-1:stop_mode=clone")
        return chain + (f",{GRADE}" if grade else "") + ",format=rgba"
    grow = ""
    if slot.get("card_in"):
        s = f"({CARD_IN_FROM}+{1 - CARD_IN_FROM:.2f}*(1-pow(1-min(t/{CARD_IN_S}\\,1)\\,2)))"
        grow = f",scale=w='trunc({lw}*{s}/2)*2':h='trunc({lh}*{s}/2)*2':eval=frame"
    rest = bottom["frame"]["outer"][1] - CARD_MARGIN             # where the bottom card's layer comes to rest
    slide = str(H)
    if not slot.get("loop_tail"):
        eased = f"(1-pow(1-clip((t-{COMPARE_SECOND_S})/{COMPARE_SLIDE_S}\\,0\\,1)\\,3))"
        slide = f"{rest}+{H - rest}*(1-{eased})"
    before, name = source_graph(slot) if top["kind"] == "video" else ("", "[0:v]")
    background = background_chain(top, frames, slot.get("bg_zoom", (1.0, 1.0)), top.get("start", 0.0))
    inset = CARD_MARGIN + CARD_BORDER
    vignette = f",{VIGNETTE}" if grade else ""
    return (f"{before}{name}split=2[a][b];[a]{background}[bg];[b]{art(top, motions[0])}[p0];"
            f"[1:v]{art(bottom, motions[1])}[p1];"
            f"[2:v]format=rgba,split=2[f0][f1];[3:v]format=gray,split=2[m0][m1];"
            f"[p0][m0]alphamerge[a0];[p1][m1]alphamerge[a1];"
            f"[f0][a0]overlay={inset}:{inset}:format=auto{grow}[c0];[f1][a1]overlay={inset}:{inset}:format=auto[c1];"
            f"[bg][c0]overlay=x='{ox + ow / 2:.0f}-w/2':y='{oy + oh / 2:.0f}-h/2':eval=frame:format=auto[one];"
            f"[one][c1]overlay=x={ox - CARD_MARGIN}:y='{slide}':eval=frame:format=auto{vignette},"
            f"scale={COLOUR_OUT}{FINISH}")


def render_segment(slot, out, encode):
    """One visual as its exact number of frames. -an: a source's own audio never comes along. A slot's grade is off
    unless render() turns it on, so the pieces render as they did before PR 17 when called on their own."""
    visual, frames = slot["visual"], slot["frames"]
    grade = slot.get("grade", False)
    still = ["-loop", "1", "-framerate", FPS]
    if visual["kind"] in ("card", "snippet"):
        behind = visual["bg"]
        inputs = picture_input(behind, slot if behind["kind"] == "video" else {}, slot.get("start_s"))
        before, name = source_graph(slot) if behind["kind"] == "video" else ("", "")
        picture = (background_chain(behind, frames, slot.get("bg_zoom", (1.0, 1.0)), behind.get("start", 0.0))
                   + (f",{VIGNETTE}" if grade else "") + f",scale={COLOUR_OUT}{FINISH}")
        how = ["-filter_complex", f"{before}{name}{picture}"] if before else ["-vf", picture]
        ffmpeg([*inputs, "-an", "-sn", "-dn", *how, "-frames:v", frames, *encode, *COLOUR_TAGS, out])
        return
    if visual["kind"] == "compare":
        top, bottom = visual["top"], visual["bottom"]
        layer, mask = card_layer(Path(out).parent, top["frame"])
        motions = slot.get("motions") or [None, None]
        inputs = [*picture_input(top, {**slot, "motion": motions[0]} if top["kind"] == "video" else {"motion": motions[0]},
                                 slot.get("start_s")),
                  *picture_input(bottom, {"motion": motions[1]}), *still, "-i", layer, *still, "-i", mask]
        ffmpeg([*inputs, "-an", "-sn", "-dn", "-filter_complex", compare_graph(slot), "-frames:v", frames, *encode,
                *COLOUR_TAGS, out])
        return
    if visual.get("layout") == "framed":
        layer, mask = card_layer(Path(out).parent, visual["frame"])
        inputs = [*picture_input(visual, slot, slot.get("start_s")), "-loop", "1", "-framerate", FPS, "-i", layer,
                  "-loop", "1", "-framerate", FPS, "-i", mask]
        ffmpeg([*inputs, "-an", "-sn", "-dn", "-filter_complex", framed_graph(slot), "-frames:v", frames, *encode,
                *COLOUR_TAGS, out])
        return
    if visual["kind"] == "image":
        inputs = picture_input(visual, slot)
        picture = image_filter(visual, slot["motion"], frames, graded(grade) + FINISH)
        how = ["-vf", picture]
    else:
        inputs = picture_input(visual, slot, slot["start_s"])
        picture = (lock_video_filter(visual, slot["motion"], grade) if visual.get("lock")
                   else video_filter(visual.get("focus", 0.5), visual.get("focus_y", 0.5), grade))
        before, name = source_graph(slot)
        how = ["-filter_complex", f"{before}{name}{picture}"] if before else ["-vf", picture]
    ffmpeg([*inputs, "-an", "-sn", "-dn", *how, "-frames:v", frames, *encode, *COLOUR_TAGS, out])


def font_warning(work):
    """A warning if libass ended up with another font for Anton or Lilita One: it would swap one in without saying so."""
    events = [dialogue(0, 0, 1, "Caption", "TEST"), dialogue(0, 0, 1, "Caption", f"{{\\fn{CHUNKY}}}TEST")]
    (work / "check.ass").write_text(ass_document(events), encoding="utf-8")
    err = ffmpeg(["-f", "lavfi", "-i", "color=black:s=64x64:d=0.1", "-frames:v", "1", "-vf", "ass=check.ass:fontsdir=.",
                  "-f", "null", "-"], cwd=work, loglevel="debug")
    picked = {m.group(1).strip(): m.group(2).strip() for m in re.finditer(r"fontselect: \((.*?),.*?\) -> ([^,\r\n]+)", err)}
    wrong = [f"{family} -> {picked.get(family, 'no font line')}" for family in (FONT, CHUNKY)
             if family.replace(" ", "") not in picked.get(family, "").replace(" ", "")]
    return f"libass didn't confirm {'; '.join(wrong)}" if wrong else None


def render(script_path, root, out_root, run_dir=None):
    """Render a script. The files go to out_root/<slug>/<time>, or to run_dir when it is given (make_short.py)."""
    started = time.perf_counter()
    if not FFMPEG or not FFPROBE:
        raise ScriptError("ffmpeg and ffprobe must be on PATH")
    script = load_script(script_path, root)
    kokoro = load_kokoro()
    warnings = script_warnings(script)
    run_dir = Path(run_dir) if run_dir else Path(out_root) / script["slug"] / datetime.now().strftime("%Y%m%d-%H%M%S")
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
        assign_backgrounds(slots)
        plan_videos(slots)
        for slot in slots:
            slot["grade"] = script["grade"]
        last_line = sum(s["frames"] for s in slots if s["line"] == len(script["lines"]) - 1 and not s.get("loop_tail"))
        warnings += slot_warnings(slots, script["lines"], last_line)
        warnings += runtime_warnings(voice_s)

        hidden = card_spans(slots, script["caption_style"])
        captions = (word_events(words, total_s, hidden, caption_places(slots)) if script["caption_style"] == "word"
                    else caption_events(words, total_s, hidden))
        events = card_events(slots, script["open_card"]) + captions + overlay_events(slots, script["open_card"])
        pill, pill_click = script.get("end_pill"), None
        if pill:
            covered = place_end_pill(pill, slots, total_frames)
            if covered:
                warnings.append(covered)
            events += end_pill_events(pill, total_frames)
            pill_click = pill_frames(total_frames)[1] / FPS
        pacing = pacing_report(visual_changes(slots, total_frames if pill else None), total_s, len(words))
        warnings += pacing["warnings"]
        (run_dir / "captions.ass").write_text(ass_document(events), encoding="utf-8")
        for font in FONT_FILES:
            shutil.copy(font, work)
        font_problem = font_warning(work)
        if font_problem:
            warnings.append(font_problem)

        t = time.perf_counter()
        music, music_note = pick_music(script["music"], root, voice_s, script["slug"])
        if script["music"] == "auto" and music is None:
            warnings.append(f'music is "auto" but {root / MUSIC_DIR} has no audio files, so this Short has no music')
        sfx = plan_sfx(slots, pill_click) if script["sfx"] else []
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
           "sfx": sfx,
           "captions": ("one word at a time, yellow; tagged words keep their colour (green tags draw cyan)"
                        if script["caption_style"] == "word" else
                        "2-4 words, the one being said turns yellow; tagged words keep their colour (green tags draw cyan)"),
           "look": {"caption_style": script["caption_style"], "grade": script["grade"],
                    "layout": script["layout"] or "framed for landscape pictures, full for portrait",
                    "captions_hidden_over_cards_s": [[round(a, 3), round(b, 3)] for a, b in hidden]},
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
                        "fill": s.get("fill"), "loop_tail": bool(s.get("loop_tail")),
                        "beat": s["visual"].get("beat"),
                        "layout": s["visual"].get("layout") or s["visual"]["kind"],
                        "card_aspect": s["visual"].get("card_aspect") if s["visual"].get("layout") == "framed" else None,
                        "card": list(s["visual"]["frame"]["outer"]) if "frame" in s["visual"] else None,
                        "label": ({k: s["visual"]["label"][k] for k in ("text", "style", "pos", "y")}
                                  if s["visual"].get("label") else None),
                        "marks": len(s["visual"].get("marks", [])),
                        "behind": s["visual"]["bg"]["source"] if "bg" in s["visual"] else None,
                        "lock": ({"zoom": round(s["visual"]["lock"]["zoom"], 2),
                                  "box_at_end": [round(n) for n in lock_rect(s["visual"]["lock"],
                                                                             lock_zoom(s["visual"]["lock"], s["frames"] - 1, s["frames"]))]}
                                 if s["visual"].get("lock") else None),
                        "compare": ({"top": list(s["visual"]["top"]["frame"]["outer"]),
                                     "bottom": list(s["visual"]["bottom"]["frame"]["outer"]),
                                     "seam_y": s["visual"]["seam_y"],
                                     "label_ys": [side["label"]["y"] if side.get("label") else None
                                                  for side in (s["visual"]["top"], s["visual"]["bottom"])]}
                                    if s["visual"]["kind"] == "compare" else None),
                        "snippet": ({"card": list(s["visual"]["snippet"]["card"]), "size": s["visual"]["snippet"]["size"],
                                     "lines": len(s["visual"]["snippet"]["lines"])}
                                    if s["visual"]["kind"] == "snippet" else None),
                        "banner": s["visual"]["banner"]["text"] if s["visual"].get("banner") else None}
                       for s in slots],
           "pacing": pacing,
           "end_pill": {"text": pill["text"], "pos": pill["pos"], "y": pill["y"], "from_s": round(pill_frames(total_frames)[0] / FPS, 3),
                        "click_s": round(pill_click, 3)} if pill else None,
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
    clicks = "".join(f", {kinds.count(kind)} {name}" for kind, name in (("lock", "lock-on clicks"), ("click", "pill clicks"))
                     if kind in kinds)
    print(f"  Sound effects: {kinds.count('whoosh')} whooshes, {kinds.count('hit')} hits{clicks}" if script["sfx"] else
          "  Sound effects: off")
    own = [s["visual"] for s in slots if not s.get("loop_tail")]
    framed = sum(1 for v in own if v.get("layout") == "framed")
    cards = sum(1 for v in own if v["kind"] == "card")
    print(f"  Look: {framed} framed, {sum(1 for v in own if v.get('layout') == 'full')} full screen, {cards} text cards; "
          f"{script['caption_style']} captions; grade {'on' if script['grade'] else 'off'}")
    beats = [v["beat"] for v in own if v.get("beat")]
    if beats:
        print("  Beats: " + ", ".join(f"{beats.count(b)} {b}" for b in BEATS if b in beats)
              + (f"; subscribe pill at the {pill['pos']}" if pill else ""))
    for line in pacing_lines(pacing):
        print(line)
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
        return f"target {MIN_TOTAL_S}-{MAX_TOTAL_S} s: {MIN_TOTAL_S - seconds:.1f} s under"
    return f"inside the {MIN_TOTAL_S}-{MAX_TOTAL_S} s target"


def runtime_warnings(seconds):
    """Outside the target warns (PR 18: 34-39 s, and both ways). A long Short loses people before the loop."""
    if seconds > MAX_TOTAL_S:
        return [f"the Short is {seconds:.1f} s, over the {MIN_TOTAL_S}-{MAX_TOTAL_S} s target: cut words rather than "
                f"speed the voice past {MAX_SPEED:g}"]
    if seconds < MIN_TOTAL_S:
        return [f"the Short is {seconds:.1f} s, under the {MIN_TOTAL_S}-{MAX_TOTAL_S} s target: about "
                f"{TARGET_WORDS[0]}-{TARGET_WORDS[1]} spoken words fill it"]
    return []


# --- The pacing checker (PR 18) ---

def visual_changes(slots, pill_frames_total=None):
    """Every moment the picture changes in a way the eye catches, as (seconds, what): each cut, and what arrives inside
    a shot (a lock-on box, a compare's second card, a snippet's marker, marks), and the subscribe pill when
    pill_frames_total (the Short's length in frames) is given. Small follow-ups don't count again: a snippet's second
    sweep, the cursor's click, a label popping in after its cut. Changes closer than CHANGE_MERGE_S to the one before
    count as part of it, so a lock-on box or a marker that follows its cut at once is that cut. A slow zoom or pan is
    movement, not a change."""
    found = []
    for i, slot in enumerate(slots):
        visual, at, end = slot["visual"], slot["start"] / FPS, slot["end"] / FPS
        if i:
            found.append((at, "the loop ending: back to the first shot" if slot.get("loop_tail")
                          else f"cut to {visual['source']}"))
        if slot.get("loop_tail"):
            continue
        inside = [(m["at"], f"a mark ({m['icon']})") for m in visual.get("marks", [])]
        if visual.get("lock"):
            inside.append((LOCK_SNAP_S, "the lock-on box lands and the zoom starts"))
        if visual["kind"] == "compare":
            inside.append((COMPARE_SECOND_S, "the second card slides in"))
        if visual["kind"] == "snippet":
            inside += [(t, "the marker sweeps the highlight") for t in visual["snippet"]["sweeps"][:1]]
        found += [(at + t, what) for t, what in inside if at + t < end - 0.1]
    if pill_frames_total:
        found.append((pill_frames(pill_frames_total)[0] / FPS, "the subscribe pill pops in"))
    counted = []
    for at, what in sorted(found):
        if not counted or at - counted[-1][0] >= CHANGE_MERGE_S - 1e-6:   # exactly that far apart is two moments
            counted.append((at, what))
    return [(round(at, 3), what) for at, what in counted]


def pacing_report(changes, total_s, words=None):
    """The pacing of a finished timeline: its length, how many visual changes it has, how many that is every 10 s,
    and the longest stretch without one (from the first frame, between changes, and to the last frame). warnings
    lists what misses the targets: 34-39 s is checked by runtime_warnings(), so here it is the rate and the gaps."""
    times = [0.0] + [at for at, _ in changes] + [total_s]
    gaps = sorted(((round(b - a, 2), round(a, 2), round(b, 2)) for a, b in zip(times, times[1:])), reverse=True)
    rate = len(changes) / total_s * 10 if total_s else 0.0
    warnings = []
    if rate < MIN_CHANGES_PER_10_S:
        warnings.append(f"pacing: {rate:.1f} visual changes every 10 s, under the target of {MIN_CHANGES_PER_10_S:g}-"
                        f"{MAX_CHANGES_PER_10_S:g}: add shots or beats")
    if rate > MAX_CHANGES_PER_10_S:
        warnings.append(f"pacing: {rate:.1f} visual changes every 10 s, over the target of {MIN_CHANGES_PER_10_S:g}-"
                        f"{MAX_CHANGES_PER_10_S:g}: it may feel frantic")
    warnings += [f"pacing: nothing changes for {gap:.1f} s, from {a:.1f} s to {b:.1f} s; the limit is {MAX_GAP_S:g} s"
                 for gap, a, b in sorted(g for g in gaps if g[0] > MAX_GAP_S + 1e-9)[::-1]]
    return {"length_s": round(total_s, 2), "length_ok": MIN_TOTAL_S <= total_s <= MAX_TOTAL_S,
            "target_s": [MIN_TOTAL_S, MAX_TOTAL_S], "changes": len(changes), "per_10_s": round(rate, 2),
            "target_per_10_s": [MIN_CHANGES_PER_10_S, MAX_CHANGES_PER_10_S],
            "longest_gap_s": gaps[0][0], "longest_gap_at_s": list(gaps[0][1:]), "max_gap_s": MAX_GAP_S,
            "words": words, "warnings": warnings,
            "events": [{"at_s": at, "what": what} for at, what in changes]}


def pacing_lines(report):
    """The pacing report as the lines the console prints after a render."""
    ok = lambda good: "ok" if good else "WARN"
    low, high = report["target_per_10_s"]
    a, b = report["longest_gap_at_s"]
    lines = ["  Pacing check:",
             f"    Length        {report['length_s']:.1f} s   (target {report['target_s'][0]}-{report['target_s'][1]} s)  "
             f"{ok(report['length_ok'])}",
             f"    Changes       {report['changes']} visual changes",
             f"    Rate          {report['per_10_s']:.1f} per 10 s   (target {low:g}-{high:g})  "
             f"{ok(low <= report['per_10_s'] <= high)}",
             f"    Longest gap   {report['longest_gap_s']:.1f} s, from {a:.1f} s to {b:.1f} s   (limit {report['max_gap_s']:g} s)  "
             f"{ok(report['longest_gap_s'] <= report['max_gap_s'] + 1e-9)}"]
    if report.get("words"):
        lines.append(f"    Words         {report['words']} spoken   (about {TARGET_WORDS[0]}-{TARGET_WORDS[1]} fills the target)")
    return lines


def print_pacing(run):
    """The pacing check of a run that is already rendered, read from its log.json, with every change it counted."""
    path = Path(run)
    path = path / "log.json" if path.is_dir() else path
    try:
        log = json.loads(path.read_text(encoding="utf-8"))
        report = log.get("pacing")
        if report is None:   # a run from before PR 18: its log has the cuts, which is most of it
            cuts = [(v["start_s"], "the loop ending: back to the first shot" if v.get("loop_tail") else f"cut to {v['source']}")
                    for v in log["visuals"][1:]]
            report = pacing_report(cuts, log["durations"]["video_s"], log.get("words"))
            report["warnings"] = runtime_warnings(log["durations"]["video_s"]) + report["warnings"]
            print("An older run: its log has only the cuts, so marks inside a shot aren't counted.")
    except (OSError, ValueError, KeyError, TypeError):
        raise ScriptError(f"{path} isn't the log.json of an explainer run") from None
    print(f"Run: {path.parent}")
    for line in pacing_lines(report):
        print(line)
    for event in report["events"]:
        print(f"      {event['at_s']:6.2f} s  {event['what']}")
    for warning in report["warnings"]:
        print(f"  WARNING: {warning}")


def main():
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Turn a script file into a finished explainer Short.")
    parser.add_argument("script", help="the script to render; with --pacing, a finished run's folder or its log.json")
    parser.add_argument("--root", type=Path, default=ROOT, help="the folder visuals and music are relative to")
    parser.add_argument("--out", type=Path, default=ROOT / "explainers", help="where the run folders go")
    parser.add_argument("--pacing", action="store_true",
                        help="print the pacing check of a finished run again, without rendering anything")
    args = parser.parse_args()
    try:
        if args.pacing:
            print_pacing(args.script)
            return
        render(args.script, args.root, args.out)
    except ScriptError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
