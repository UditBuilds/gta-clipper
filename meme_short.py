"""Meme Shorts (PR 39, Nov 27): a funny GTA 6 moment from the owner's own footage, with the game's own sound, one static
caption on top and almost no editing. A separate mode: narrated Shorts (/launch, engine v4) are not touched.

Why: the narrated Shorts went flat (203-1,241 views), the hits were physics chaos with a quick payoff, and a channel of
funny GTA 6 character moments gets millions of views a Short with real game audio, one caption and no editing. The rules
below come from the owner's frame-by-frame teardown of 12 of those Shorts; each is a check (memetest.py).

  .\\.venv\\Scripts\\python.exe meme_short.py find [--top 15]          funny candidates, from the index and bank (read only)
  .\\.venv-tts\\Scripts\\python.exe meme_short.py hear REC FROM TO      the dialogue in a window (Whisper), with swear words
  .\\.venv\\Scripts\\python.exe meme_short.py check SPEC.json           the rules only; renders nothing
  .\\.venv-tts\\Scripts\\python.exe meme_short.py render SPEC.json --out FOLDER

A spec (times are seconds of the recording):
  {"recording": "Grand Theft Auto VI_20261121164843", "from_s": 1744.0, "to_s": 1753.0, "kind": "gag",
   "motion_s": 1744.4, "punchline_s": 1747.6, "reaction_end_s": 1752.0, "heard": [{"from_s", "to_s", "text"}],
   "layout": "full" | "letterbox", "focus": [across, down],
   "captions": ["three", "caption", "options"], "star": 0, "swap": {"text": "THAT strong", "at_s": 1748.0},
   "edit": null | {"type": "punch", "at_s", "for_s", "zoom"} | {"type": "replay", "back_s", "for_s"},
   "music": null | "<a file in C:\\AI\\gta\\music>", "title": "..."}

The rules (the owner's, Nov 27):
  1 length: a gag (physics or sight) 7-12 s; dialogue that builds to one payoff 15-24 s; never over 30 s.
  2 first second: motion or an impact by 1.0 s, or the first spoken line by 0.5 s; a gag's punchline at 0:02-0:06.
  3 caption: one, on top, 2-6 words (10 for "<Subject> tried <X> and this happens..."), from 0.0 s to the last frame,
    one optional swap after the payoff; white bold sans (Arial Bold), a 4 px black outline, no box, its middle at y
    14-22 % of 1920 (inside the top bar when letterboxed); never "Watch the X" or "Can you X?"; swears censored (fkng,
    sht); no emoji in v1 (no colour-emoji renderer without a new package), so never an empty box.
  4 layout: full-bleed 9:16 centred on the subject, or the 16:9 picture letterboxed; never a blurred background.
  5 sound: no AI voice; the game's own sound at -14 LUFS through the repo's true-peak limit; an optional "wrong mood"
    music bed (off unless named), 20 dB under the game while a line is heard.
  6 edits: none by default; at most one punch-in (1.20-1.35x toward the subject at the punchline for 1-2 s) or one
    replay (back 0.5-1 s, 0.5x, 2-3.5 s); no whooshes, booms, arrows, inserts or freeze frames.
  7 ending: a hard cut 0.5-1.5 s after the punchline or the last reaction line; no end card, no subscribe pill.
  8 title: short, no hashtags, the caption's voice.
Kept from the repo (rule 9): footage that is clean in the index and was never used or rejected; never-shown footage;
no blood in the first 7 s (the vision model twice a second and the index's lines); true peak under -1 dBTP. Swearing in
the game's own sound is allowed and flagged.

The finder and the checks read the index, the bank and _used_footage.json; nothing here writes to them. A render writes
its own folder only: short.mp4, captions.txt, title.txt and meme.json (not render.json, which /launch reads).
"""
import argparse
import array
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import clip
import explainer as ex
import fastff
import footage_short
import heavy
import index
import library
import proof
import safety
import truth

W, H, FPS = 1080, 1920, 30
ROOT = Path(r"C:\AI\gta")
SHORTS = ROOT / "shorts"
BANK = ROOT / "bank" / "content_bank.json"
MUSIC_DIR = ROOT / "music"
LOG_NAME = "meme.json"                    # not render.json: launch.failed_before reads every render.json under SHORTS

# Rule 1: length
KINDS = {"gag": (7.0, 12.0), "dialogue": (15.0, 24.0)}
HARD_CAP_S = 30.0
# Rule 2: the first second
MOTION_BY_S, LINE_BY_S, PUNCH_AT = 1.0, 0.5, (2.0, 6.0)
# Rule 2 measured on the cut itself (measure_start), every STEP_S: the picture's change between frames (a 96x54 gray
# copy) and the sound's level. The first second passes when its busiest 0.2 s moves at least START_PIC_SHARE of the
# rest's busy level (its START_PCT percentile), or when its first LINE_BY_S of sound comes within START_SND_DB of the
# rest's loud level. The rest is the rest of the Short (a replay included), and its busy level is the 90th percentile:
# the 75th sat on the lead-in of a 7 s gag, whose punch fills less than a quarter of it. Set on the owner's footage
# (Nov 28): sample 1's picture changes 20.24 (needs 6.05); sample 2's picture is still but its line at 0.4 s is -26.3 dB
# (needs -27.9); Jason standing still by the bear truck at 4:30 is 0.96 (needs 6.32) and -32.1 dB (needs -20.5).
STEP_S, START_PCT, START_PIC_SHARE, START_SND_DB = 0.1, 0.9, 0.5, 3.0
MEASURE_W, MEASURE_H, MEASURE_FPS, MEASURE_RATE = 96, 54, 30, 8000
# Rule 3: the caption
WORDS, SETUP_WORDS, SWAP_WORDS = (2, 6), 10, (1, 6)
ARTICLES = ("a", "an", "the")              # not counted (counted_words)
SETUP = re.compile(r"^\s*\S.*\btried\b.+\band this happens\.\.\.\s*$", re.I)
NEVER = ((re.compile(r"^\W*watch\b", re.I), "never \"Watch the X\""), (re.compile(r"\bcan you\b", re.I),
                                                                      "never \"Can you X?\""))
PLAIN = re.compile(r"^[A-Za-z0-9 .,!?'\"\u2019:;&()$%+\-/]*$")   # what v1 draws: no emoji, no symbols
FONT, FONT_FILE = "Arial", Path(r"C:\Windows\Fonts\arialbd.ttf")   # white bold sans: Arial Bold, Windows' own
CAPTION_SIZE, OUTLINE_PX = 76, 4
LINE_H = 1.17                             # a line's height in font sizes (Arial)
LINE_CHARS = 20                           # characters a line at 76 px: about 880 px of Arial Bold, inside x 60-1020
CAPTION_Y = 346                           # full-bleed: the caption's middle at 18 % of 1920
Y_SHARE = (0.14, 0.22)
# Rule 4: layout
LAYOUTS = ("full", "letterbox")
# Rule 5: sound
TARGET_LUFS, LUFS_TOLERANCE = ex.TARGET_LUFS, 1.0
TRUE_PEAK_MAX = safety.TRUE_PEAK_MAX
MUSIC_DUCK_DB, MUSIC_BED_DB, DUCK_RAMP_S = -20.0, -12.0, 0.15   # the bed against the game: under a line, between lines
# Rule 6: edits
PUNCH_ZOOM, PUNCH_FOR, PUNCH_NEAR = (1.20, 1.35), (1.0, 2.0), (-0.5, 0.2)  # NEAR: its start against the punchline
REPLAY_BACK, REPLAY_SPEED, REPLAY_FOR = (0.5, 1.0), 0.5, (2.0, 3.5)
REPLAY_AFTER = (0.2, 1.5)                 # the jump back comes this long after the punchline was seen
# Rule 7: ending
END_AFTER = (0.5, 1.5)
# Rule 8: title
TITLE_CHARS, TITLE_WORDS = 60, (2, 12)

KEYS = {"recording", "from_s", "to_s", "kind", "motion_s", "punchline_s", "reaction_end_s", "heard", "layout", "focus",
        "captions", "star", "swap", "edit", "music", "title", "slug", "what", "why", "notes"}
BANNED = ((4, re.compile(r"blur|background|^bg$"), "no blurred background in meme mode"),
          (5, re.compile(r"voice|^lines$|script|narrat|tts|^say$|speed"), "no AI voice: the game's own sound only"),
          (6, re.compile(r"whoosh|boom|sfx|sound_effect|arrow|freeze|insert|shake|flash|transition|^edits$|zoom"),
           "no whooshes, booms, arrows, inserts, freeze frames or a second edit"),
          (7, re.compile(r"end_card|end_pill|pill|subscribe|outro|loop"), "a hard cut: no end card, no subscribe pill"))

# Swears are written censored (the owner's fkng, sht: censor()); slurs are never written at all.
SLURS = re.compile(r"(?<![a-z])(nigg\w*|fag|fags|faggot\w*|retard\w*)(?![a-z])", re.I)


class MemeError(Exception):
    """Something the spec or the footage must fix; nothing was written."""


# --- The rules (pure: a spec in, problems out) ---

def words_in(text):
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'\u2019]*", text or "")


def counted_words(text):
    """The words rule 3 counts: every word but "a", "an" and "the". The owner's own example "Jason Became a Gorilla for a
    Sec" is 7 words with its articles and 5 without, and the rule says 2-6, so articles don't count (Nov 27 call)."""
    return [w for w in words_in(text) if w.lower() not in ARTICLES]


def censor(text):
    """The text with its swear words written the owner's way (fucking -> fkng, shit -> sht, bitch -> btch), the case
    kept. A slur is left as it is, so text_problems() refuses it: it is never written, censored or not."""
    def fix(m):
        w = m.group()
        if SLURS.fullmatch(w) or not safety.profanity(w):
            return w
        if "shit" in w.lower():
            return re.sub(r"(?i)(sh)i(t)", r"\1\2", w)
        if re.search(r"(?i)f+u+c+k", w):
            w = re.sub(r"(?i)f+u+c+k+", lambda k: "FK" if k.group().isupper() else "Fk" if k.group()[0].isupper() else "fk",
                       w)
            return re.sub(r"(?i)(fk)([a-z]*)", lambda k: k.group(1) + re.sub(r"[aeiouAEIOU]", "", k.group(2)), w)
        if w.lower() in ("ass", "asses"):
            return w.replace("ss", "zz").replace("SS", "ZZ")
        return w[0] + re.sub(r"[aeiouAEIOU]", "", w[1:])
    return re.sub(r"[A-Za-z][A-Za-z']*", fix, text)


def text_problems(text, where):
    """What any text on screen or in the title may not be: emoji or symbols, a plain swear word, a slur, a trailer name,
    a story word."""
    out = []
    if not isinstance(text, str) or not text.strip():
        return [f"{where} is empty"]
    if not PLAIN.match(text):
        odd = sorted({c for c in text if not PLAIN.match(c)})
        out.append(f"{where} has {''.join(odd)!r}: v1 draws no emoji or symbols (they would be empty boxes)")
    if SLURS.search(text):
        out.append(f"{where} has a slur: never written, censored or not")
    elif safety.profanity(text):
        out.append(f"{where} has {', '.join(safety.profanity(text))}: write it censored ({censor(text)!r})")
    out += truth.name_problems(text, "", where, title_case=True)
    out += truth.spoiler_problems(text, where)
    return out


def caption_problems(text, swap=False):
    """Rule 3 for one caption (or the swap): 2-6 words (1-6 for a swap), up to 10 for "<Subject> tried <X> and this
    happens...", never "Watch the X" or "Can you X?", plain characters, censored."""
    where = "the swap" if swap else f"caption {text!r}"
    out = [f"rule 3: {p}" for p in text_problems(text, where)]
    if not isinstance(text, str):
        return out
    n = len(counted_words(text))
    lo, hi = SWAP_WORDS if swap else WORDS
    if not swap and SETUP.match(text):
        hi = SETUP_WORDS
    if not lo <= n <= hi:
        out.append(f"rule 3: {where} has {n} words (a, an and the not counted); a caption has {lo}-{hi}"
                   + ("" if swap or hi == SETUP_WORDS else f" ({SETUP_WORDS} only for \"<Subject> tried <X> and this "
                                                           "happens...\")"))
    out += [f"rule 3: {where}: {why}" for pat, why in NEVER if pat.search(text)]
    return out


def title_problems(title):
    """Rule 8: short (TITLE_CHARS at most, 2 words at least), no hashtag or @handle, the caption's rules for words."""
    out = [f"rule 8: {p}" for p in text_problems(title, "the title")]
    if not isinstance(title, str):
        return out
    if "#" in title:
        out.append("rule 8: no hashtags in the title (they go in the description)")
    if "@" in title:
        out.append("rule 8: no @handle in the title")
    if len(title) > TITLE_CHARS:
        out.append(f"rule 8: the title is {len(title)} characters; at most {TITLE_CHARS}")
    n = len(words_in(title))
    if not TITLE_WORDS[0] <= n <= TITLE_WORDS[1]:
        out.append(f"rule 8: the title has {n} words; {TITLE_WORDS[0]}-{TITLE_WORDS[1]}")
    out += [f"rule 8: the title: {why}" for pat, why in NEVER if pat.search(title)]
    return out


def caption_of(spec):
    """The starred caption."""
    return spec["captions"][spec["star"]]


def music_of(spec):
    return spec.get("music") or None


def replay_of(spec):
    e = spec.get("edit")
    return e if isinstance(e, dict) and e.get("type") == "replay" else None


def punch_of(spec):
    e = spec.get("edit")
    return e if isinstance(e, dict) and e.get("type") == "punch" else None


def timeline(spec):
    """The Short's own times (seconds from its first frame): main part, duration, punchline (and the replay's own),
    what the ending is measured from, the swap, the first motion and the first line."""
    a, b = float(spec["from_s"]), float(spec["to_s"])
    main = b - a
    rep = replay_of(spec)
    punch = float(spec["punchline_s"]) - a
    out = {"main_s": round(main, 3), "duration_s": round(main + (float(rep["for_s"]) if rep else 0.0), 3),
           "punch_t": round(punch, 3), "replay_t": round(main, 3) if rep else None}
    if rep:
        out["last_punch_t"] = round(main + float(rep["back_s"]) / REPLAY_SPEED, 3)
        out["end_anchor_t"] = out["last_punch_t"]
    else:
        react = spec.get("reaction_end_s")
        out["last_punch_t"] = out["punch_t"]
        out["end_anchor_t"] = round(max(punch, float(react) - a if react is not None else punch), 3)
    swap = spec.get("swap")
    out["swap_t"] = round(float(swap["at_s"]) - a, 2) if isinstance(swap, dict) and "at_s" in swap else None
    out["motion_t"] = round(float(spec["motion_s"]) - a, 3) if spec.get("motion_s") is not None else None
    heard = spec.get("heard") or []
    out["first_line_t"] = round(min(float(h["from_s"]) for h in heard) - a, 3) if heard else None
    return out


def key_problems(spec):
    out = []
    for k in spec:
        for rule_n, pat, why in BANNED:
            if pat.search(k.lower()):
                out.append(f"rule {rule_n}: \"{k}\": {why}")
                break
        else:
            if k not in KEYS:
                out.append(f"spec: unknown key \"{k}\" (known: {', '.join(sorted(KEYS))})")
    return out


def length_problems(spec, tl):
    kind = spec.get("kind")
    if kind not in KINDS:
        return [f"rule 1: kind is {kind!r}; it is \"gag\" (physics or a sight gag) or \"dialogue\" (talk that builds to "
                "one payoff)"]
    lo, hi = KINDS[kind]
    d = tl["duration_s"]
    out = []
    if d > HARD_CAP_S:
        out.append(f"rule 1: {d:g} s is over the hard cap of {HARD_CAP_S:g} s")
    if not lo - 1e-6 <= d <= hi + 1e-6:
        out.append(f"rule 1: a {kind} lasts {lo:g}-{hi:g} s; this one is {d:g} s")
    return out


def first_second_problems(spec, tl):
    out = []
    motion = tl["motion_t"] is not None and -1e-6 <= tl["motion_t"] <= MOTION_BY_S + 1e-6
    line = tl["first_line_t"] is not None and -1e-6 <= tl["first_line_t"] <= LINE_BY_S + 1e-6
    if not (motion or line):
        out.append(f"rule 2: nothing starts in time: motion or an impact by {MOTION_BY_S:g} s (motion_s is at "
                   f"{tl['motion_t']}), or the first spoken line by {LINE_BY_S:g} s (at {tl['first_line_t']}); trim the "
                   "start")
    if not 0 < tl["punch_t"] < tl["main_s"]:
        out.append(f"rule 2: the punchline ({tl['punch_t']:g} s) is not inside the footage (0-{tl['main_s']:g} s)")
    elif spec.get("kind") == "gag" and not PUNCH_AT[0] - 1e-6 <= tl["punch_t"] <= PUNCH_AT[1] + 1e-6:
        out.append(f"rule 2: a gag's punchline lands at {PUNCH_AT[0]:g}-{PUNCH_AT[1]:g} s; this one at {tl['punch_t']:g} s")
    return out


def caption_rule_problems(spec, tl):
    out = []
    caps, star = spec.get("captions"), spec.get("star")
    if not isinstance(caps, list) or len(caps) != 3:
        out.append("rule 3: write exactly 3 caption options (\"captions\") and star one (\"star\": 0, 1 or 2)")
    if not isinstance(star, int) or isinstance(star, bool) or not isinstance(caps, list) or not 0 <= star < len(caps):
        out.append(f"rule 3: \"star\" is {star!r}; it picks one of the options (0, 1 or 2)")
    for c in caps if isinstance(caps, list) else []:
        out += caption_problems(c)
    swap = spec.get("swap")
    if swap is not None:
        if not isinstance(swap, dict) or set(swap) - {"text", "at_s"} or "text" not in swap or "at_s" not in swap:
            out.append("rule 3: one swap at most: {\"text\": ..., \"at_s\": ...}")
        else:
            out += caption_problems(swap["text"], swap=True)
            if not tl["punch_t"] - 1e-6 <= tl["swap_t"] < tl["duration_s"] - 0.5:
                out.append(f"rule 3: the swap comes after the payoff ({tl['punch_t']:g} s) and before the end; it is at "
                           f"{tl['swap_t']:g} s")
    return out


def layout_problems(spec):
    out = []
    lay = spec.get("layout")
    if lay not in LAYOUTS:
        out.append(f"rule 4: layout is {lay!r}; it is \"full\" (9:16 on the subject) or \"letterbox\" (the 16:9 "
                   "picture with black bars, for wide or sideways action); never a blurred background")
    focus = spec.get("focus")
    need = lay == "full" or punch_of(spec)
    if focus is None and need:
        out.append("rule 4: \"focus\": [across, down] of the subject (0-1) is needed to centre the crop on it")
    elif focus is not None and (not isinstance(focus, (list, tuple)) or len(focus) != 2
                                or not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in focus)):
        out.append(f"rule 4: focus is {focus!r}; it is [across, down], each 0-1")
    return out


def audio_problems(spec):
    m = music_of(spec)
    if m is None:
        return []
    if not isinstance(m, str) or re.search(r"[\\/:]", m) or Path(m).suffix.lower() not in ex.MUSIC_EXTS:
        return [f"rule 5: music is {m!r}; it is a file name in {MUSIC_DIR} (or null, the default)"]
    return []


def edit_problems(spec, tl):
    e = spec.get("edit")
    if e is None:
        return []
    if not isinstance(e, dict) or e.get("type") not in ("punch", "replay"):
        return ["rule 6: one edit at most: {\"type\": \"punch\", ...} or {\"type\": \"replay\", ...} (or none)"]
    out = []

    def within(name, v, lo, hi):
        if not isinstance(v, (int, float)) or not lo - 1e-6 <= v <= hi + 1e-6:
            out.append(f"rule 6: {e['type']} {name} is {v!r}; it is {lo:g}-{hi:g}")
    if e["type"] == "punch":
        extra = set(e) - {"type", "at_s", "for_s", "zoom"}
        within("zoom", e.get("zoom"), *PUNCH_ZOOM)
        within("for_s", e.get("for_s"), *PUNCH_FOR)
        at = e.get("at_s")
        if isinstance(at, (int, float)):
            near = float(at) - float(spec["punchline_s"])
            if not PUNCH_NEAR[0] - 1e-6 <= near <= PUNCH_NEAR[1] + 1e-6:
                out.append(f"rule 6: the punch-in starts {near:+.2f} s from the punchline; at the punchline "
                           f"({PUNCH_NEAR[0]:+g} to {PUNCH_NEAR[1]:+g} s)")
            elif isinstance(e.get("for_s"), (int, float)) and min(float(at) + e["for_s"], spec["to_s"]) - float(at) < \
                    PUNCH_FOR[0] - 1e-6:
                out.append("rule 6: the punch-in is cut short by the end; it shows 1-2 s")
        else:
            out.append("rule 6: the punch-in needs at_s, the second it starts")
    else:
        extra = set(e) - {"type", "back_s", "for_s", "speed"}
        within("back_s", e.get("back_s"), *REPLAY_BACK)
        within("for_s", e.get("for_s"), *REPLAY_FOR)
        if e.get("speed", REPLAY_SPEED) != REPLAY_SPEED:
            out.append(f"rule 6: a replay plays at {REPLAY_SPEED:g}x; this one at {e.get('speed')!r}")
        after = tl["main_s"] - tl["punch_t"]
        if not REPLAY_AFTER[0] - 1e-6 <= after <= REPLAY_AFTER[1] + 1e-6:
            out.append(f"rule 6: the replay jumps back {after:.2f} s after the punchline; "
                       f"{REPLAY_AFTER[0]:g}-{REPLAY_AFTER[1]:g} s")
    if extra:
        out.append(f"rule 6: the {e['type']} has {', '.join(sorted(extra))}: nothing else is allowed")
    return out


def ending_problems(spec, tl):
    after = tl["duration_s"] - tl["end_anchor_t"]
    out = []
    if not END_AFTER[0] - 1e-6 <= after <= END_AFTER[1] + 1e-6:
        what = "the replay's punchline" if replay_of(spec) else "the punchline or the last reaction line"
        out.append(f"rule 7: the hard cut comes {after:.2f} s after {what}; {END_AFTER[0]:g}-{END_AFTER[1]:g} s")
    react = spec.get("reaction_end_s")
    if react is not None and not replay_of(spec) and float(react) < float(spec["punchline_s"]) - 1e-6:
        out.append("rule 7: reaction_end_s is before the punchline")
    return out


def problems(spec):
    """Every rule's problems for a spec, each starting "rule N:" (or "spec:"); [] when it may be rendered."""
    if not isinstance(spec, dict):
        return ["spec: not a JSON object"]
    out = key_problems(spec)
    try:
        for k in ("from_s", "to_s", "punchline_s"):
            float(spec[k])
        tl = timeline(spec)
    except (KeyError, TypeError, ValueError) as e:
        return out + [f"spec: from_s, to_s and punchline_s are numbers (and the swap, edit and heard times): {e}"]
    if tl["main_s"] <= 0:
        return out + ["spec: to_s is not after from_s"]
    out += length_problems(spec, tl)
    out += first_second_problems(spec, tl)
    out += caption_rule_problems(spec, tl)
    out += layout_problems(spec)
    out += audio_problems(spec)
    out += edit_problems(spec, tl)
    out += ending_problems(spec, tl)
    out += title_problems(spec.get("title"))
    return out


# --- Caption: where, when, what it looks like ---

def wrap(text, chars=LINE_CHARS):
    """The caption in as few lines as fit `chars`, as even as can be."""
    ws = text.split()
    n = 1
    while True:
        best = None
        for cuts in _splits(len(ws), n):
            lines = [" ".join(ws[i:j]) for i, j in zip((0,) + cuts, cuts + (len(ws),))]
            worst = max(len(x) for x in lines)
            if best is None or worst < best[0]:
                best = (worst, lines)
        if best[0] <= chars or n >= len(ws):
            return best[1]
        n += 1


def _splits(count, n):
    if n == 1:
        return [()]
    out = []
    for first in range(1, count - n + 2):
        out += [(first,) + tuple(first + c for c in rest) for rest in _splits(count - first, n - 1)]
    return out


def caption_y(spec, sw=1920, sh=1080):
    """The caption's middle: CAPTION_Y full-bleed, the top bar's middle when letterboxed."""
    if spec.get("layout") == "letterbox":
        return geometry(spec, sw, sh)["picture_y"] / 2
    return CAPTION_Y


def caption_box(spec, sw=1920, sh=1080):
    """(top, bottom) in pixels of the caption at its tallest (the starred one or the swap), outline included."""
    texts = [caption_of(spec)] + ([spec["swap"]["text"]] if isinstance(spec.get("swap"), dict) else [])
    n = max(len(wrap(t)) for t in texts)
    half = n * LINE_H * CAPTION_SIZE / 2 + OUTLINE_PX
    y = caption_y(spec, sw, sh)
    return (round(y - half), round(y + half))


def caption_events(spec):
    """[(start_s, end_s, text)]: the caption from 0.00 s to past the last frame, or until the swap and the swap from the
    same centisecond to past the last frame. Never toggled, never more than one swap."""
    tl = timeline(spec)
    end = round(tl["duration_s"] + 1.0, 2)           # past the last frame: an event ending on it can miss it
    cap = caption_of(spec)
    if tl["swap_t"] is None:
        return [(0.0, end, cap)]
    return [(0.0, tl["swap_t"], cap), (tl["swap_t"], end, spec["swap"]["text"])]


def ass_time(seconds):
    cs = round(seconds * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ass_document(spec, sw=1920, sh=1080):
    """The captions as ASS for libass: a 1080x1920 canvas (a pixel is a pixel), Arial Bold, white, a 4 px black outline,
    no shadow, BorderStyle 1 (an outline; 3 would be a box), centred on caption_y."""
    y = round(caption_y(spec, sw, sh))
    style = (f"Style: Meme,{FONT},{CAPTION_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,"
             f"{OUTLINE_PX},0,5,60,60,0,1")
    events = [f"Dialogue: 0,{ass_time(a)},{ass_time(b)},Meme,,0,0,0,,{{\\pos({W // 2},{y})}}"
              + "\\N".join(ex.escape_ass(line) for line in wrap(text)) for a, b, text in caption_events(spec)]
    return "\n".join(["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2",
                      "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "", "[V4+ Styles]",
                      "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
                      "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                      "Alignment, MarginL, MarginR, MarginV, Encoding", style, "", "[Events]",
                      "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text", *events, ""])


# --- Picture: the layout and the punch-in ---

def even(v):
    return int(round(v / 2)) * 2


def geometry(spec, sw, sh, zoom=1.0):
    """Where the picture comes from and goes, for a source sw x sh. full: "crop" (w, h, x, y), the 9:16 window centred on
    the subject's x (and, zoomed, on its y), kept inside the picture. letterbox: "src" (w, h, x, y) of the source shown,
    "picture" (w, h) on screen at "picture_y"."""
    fx, fy = (spec.get("focus") or [0.5, 0.5])
    if spec.get("layout") == "letterbox":
        pw, ph = W, even(W * sh / sw)
        cw, ch = even(sw / zoom), even(sh / zoom)
        x = min(max(fx * sw - cw / 2, 0), sw - cw)
        y = min(max(fy * sh - ch / 2, 0), sh - ch)
        return {"src": (cw, ch, int(round(x)), int(round(y))), "picture": (pw, ph), "picture_y": (H - ph) // 2}
    ch = even(sh / zoom)
    cw = even(proof.crop_width(sh) / zoom)
    x = min(max(fx * sw - cw / 2, 0), sw - cw)
    y = min(max(fy * sh - ch / 2, 0), sh - ch) if zoom != 1.0 else 0
    return {"crop": (cw, ch, int(round(x)), int(round(y)))}


def picture_filter(spec, sw, sh, zoom=1.0, fix=""):
    g = geometry(spec, sw, sh, zoom)
    if "crop" in g:
        cw, ch, x, y = g["crop"]
        return f"crop={cw}:{ch}:{x}:{y},scale={W}:{H}:flags=lanczos{fix},setsar=1"
    cw, ch, x, y = g["src"]
    pw, ph = g["picture"]
    return (f"crop={cw}:{ch}:{x}:{y},scale={pw}:{ph}:flags=lanczos{fix},pad={W}:{H}:0:{g['picture_y']}:black,"
            "setsar=1")


def frames(t):
    return int(round(t * FPS))


def graph(spec, sw, sh, fix=""):
    """ffmpeg's filter graph: input 0 is the main part (from from_s), input 1 the replay's footage. Out: [v] and [a]."""
    tl = timeline(spec)
    main_n = frames(tl["main_s"])
    # The sound is cut to the same whole frames as the picture: cut to the seconds, it ends first whenever the length
    # rounds up to a frame, and the final mux's -shortest then drops the last frames the B-frames hold back.
    parts, audio = [], [f"[0:a]aresample={ex.RATE},atrim=end_sample={round(main_n / FPS * ex.RATE)},asetpts=N/SR/TB[a0]"]
    p = punch_of(spec)
    if p:
        p0 = frames(float(p["at_s"]) - float(spec["from_s"]))
        p1 = min(main_n, p0 + frames(p["for_s"]))
        cuts = [(0, p0, 1.0), (p0, p1, float(p["zoom"])), (p1, main_n, 1.0)]
    else:
        cuts = [(0, main_n, 1.0)]
    cuts = [c for c in cuts if c[1] > c[0]]
    chain = [f"[0:v]setpts=PTS-STARTPTS,fps={FPS},split={len(cuts)}" + "".join(f"[m{i}]" for i in range(len(cuts)))
             if len(cuts) > 1 else f"[0:v]setpts=PTS-STARTPTS,fps={FPS}[m0]"]
    for i, (f0, f1, z) in enumerate(cuts):
        chain.append(f"[m{i}]trim=start_frame={f0}:end_frame={f1},setpts=PTS-STARTPTS,"
                     f"{picture_filter(spec, sw, sh, z, fix)}[v{i}]")
        parts.append(f"[v{i}]")
    rep = replay_of(spec)
    if rep:
        n = frames(rep["for_s"])
        chain.append(f"[1:v]setpts={1 / REPLAY_SPEED:g}*(PTS-STARTPTS),fps={FPS},trim=end_frame={n},setpts=PTS-STARTPTS,"
                     f"{picture_filter(spec, sw, sh, 1.0, fix)}[vr]")
        parts.append("[vr]")
        audio.append(f"[1:a]aresample={ex.RATE},asetrate={ex.RATE * REPLAY_SPEED:g},aresample={ex.RATE},"
                     f"atrim=end_sample={round(n / FPS * ex.RATE)},asetpts=N/SR/TB[a1]")
        audio.append("[a0][a1]concat=n=2:v=0:a=1[a]")
    else:
        audio[0] = audio[0].replace("[a0]", "[a]")
    chain.append(("".join(parts) + f"concat=n={len(parts)}:v=1:a=0," if len(parts) > 1 else parts[0]
                  + "null,") + "ass=captions.ass:fontsdir=.,format=yuv420p[v]")
    return ";".join(chain + audio)


# --- Sound ---

def music_gain_db(t, heard):
    """The bed's level against the game's at t: MUSIC_DUCK_DB while a line is heard, MUSIC_BED_DB between lines, with
    DUCK_RAMP_S ramps."""
    d = 0.0
    for h in heard or []:
        a, b = float(h["from_s"]), float(h["to_s"])
        d = max(d, min(1.0, max(0.0, min((t - (a - DUCK_RAMP_S)) / DUCK_RAMP_S, ((b + DUCK_RAMP_S) - t) / DUCK_RAMP_S))))
    return MUSIC_BED_DB + (MUSIC_DUCK_DB - MUSIC_BED_DB) * d


def music_expr(heard, base_db):
    """music_gain_db as an ffmpeg volume expression (t in the Short's seconds), plus base_db."""
    r = DUCK_RAMP_S
    duck = "0"
    for h in heard or []:
        a, b = float(h["from_s"]), float(h["to_s"])
        duck = f"max({duck},clip(min((t-{a - r:.3f})/{r:g},({b + r:.3f}-t)/{r:g}),0,1))"
    return f"pow(10,({base_db + MUSIC_BED_DB:.3f}+({MUSIC_DUCK_DB - MUSIC_BED_DB:g})*{duck})/20)"


def short_heard(spec):
    """The heard lines in the Short's seconds (the main part only: the replay's sound is the same lines slowed)."""
    a, b = float(spec["from_s"]), float(spec["to_s"])
    return [{"from_s": max(0.0, float(h["from_s"]) - a), "to_s": min(b, float(h["to_s"])) - a, "text": h.get("text", "")}
            for h in spec.get("heard") or [] if float(h["to_s"]) > a and float(h["from_s"]) < b]


def mix_sound(spec, game_wav, work, music_file=None):
    """game_wav (and the bed) at TARGET_LUFS through explainer's limiter: work/mix.wav. Returns what was done."""
    game_lufs = ex.measure_loudness(game_wav)[0]
    info = {"game_lufs": round(game_lufs, 1), "music": None}
    src = game_wav
    if music_file:
        d = timeline(spec)["duration_s"]
        bed_raw = work / "bed.wav"
        ex.ffmpeg(["-stream_loop", "-1", "-i", music_file, "-t", f"{d:.3f}", "-vn", "-ac", "2", "-ar", ex.RATE,
                   "-c:a", "pcm_s16le", bed_raw])
        music_lufs = ex.measure_loudness(bed_raw)[0]
        base = game_lufs - music_lufs
        expr = music_expr(short_heard(spec), base)
        mixed = work / "game_music.wav"
        ex.ffmpeg(["-i", game_wav, "-i", bed_raw, "-filter_complex",
                   f"[1:a]volume='{expr}':eval=frame,afade=t=in:d=0.3,afade=t=out:st={max(0.0, d - 0.08):.3f}:d=0.08[m];"
                   "[0:a][m]amix=inputs=2:duration=first:normalize=0[a]", "-map", "[a]", "-c:a", "pcm_s16le", mixed])
        info["music"] = {"file": str(music_file), "music_lufs": round(music_lufs, 1),
                         "between_lines_db": MUSIC_BED_DB, "under_lines_db": MUSIC_DUCK_DB}
        src = mixed
    level = ex.measure_loudness(src)[0]
    info["mix_lufs"] = round(ex.normalise(["-i", src], work / "mix.wav", level), 2)
    return info


# --- Measuring the finished file ---

def gray_frames(video, numbers, w=W, h=H):
    """{n: the gray pixels of frame n} for these frame numbers, picked by number in one pass (a seek by time can land a
    frame off, and past the end on the last one)."""
    numbers = sorted(set(int(n) for n in numbers))
    pick = "+".join(f"eq(n\\,{n})" for n in numbers)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"select={pick}", "-fps_mode", "passthrough",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
    size = w * h
    return {n: raw[i * size:(i + 1) * size] for i, n in enumerate(numbers) if len(raw) >= (i + 1) * size}


def white_box(raw, w=W, h=H, level=235, step=2, need=4):
    """(top, bottom, left, right) of the near-white pixels in a gray frame, or None."""
    rows = [y for y in range(0, h, 1) if sum(1 for x in range(0, w, step) if raw[y * w + x] >= level) >= need]
    if not rows:
        return None
    cols = [x for x in range(0, w) if any(raw[y * w + x] >= level for y in range(rows[0], rows[-1] + 1, step))]
    return (rows[0], rows[-1], cols[0], cols[-1])


def measure_caption(spec, work, duration, sw=1920, sh=1080):
    """The caption layer alone (captions.ass over black, as long as the Short), measured on its first frame, its last
    frame and both sides of the swap: each box, and whether rule 3's place holds on every one."""
    layer = work / "caption_layer.mp4"
    ex.ffmpeg(["-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d={duration:.3f}", "-vf",
               "ass=captions.ass:fontsdir=.,format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0",
               layer.name], cwd=work)
    tl = timeline(spec)
    last = frames(duration) - 1
    at = {"first": 0, "last": last}
    if tl["swap_t"] is not None:
        at.update(before_swap=frames(tl["swap_t"]) - 1, after_swap=frames(tl["swap_t"]))
    got = gray_frames(layer, at.values())
    boxes = {k: white_box(got[n]) if n in got else None for k, n in at.items()}
    lb = spec.get("layout") == "letterbox"
    top_bar = geometry(spec, sw, sh)["picture_y"] if lb else 0
    good = all(boxes.values()) and all(
        (b[1] < top_bar if lb else Y_SHARE[0] * H <= (b[0] + b[1]) / 2 <= Y_SHARE[1] * H) and 60 <= b[2] and b[3] <= W - 60
        for b in boxes.values() if b)
    return {"boxes": boxes, "ok": good}


def measure(short, spec, work, sw=1920, sh=1080):
    info = ex.probe(short)
    v = ex.stream_of(info, "video") or {}
    duration = int(v.get("nb_frames") or 0) / FPS or float(info["format"]["duration"])
    lufs, _ = ex.measure_loudness(short)
    return {"duration_s": round(duration, 3), "lufs": round(lufs, 2), "true_peak_db": safety.true_peak(short),
            "caption": measure_caption(spec, work, duration, sw, sh)}


# --- Rule 2 measured: the first second of the cut against the rest of it ---

def percentile(values, q):
    v = sorted(values)
    return v[min(len(v) - 1, int(q * len(v)))] if v else 0.0


def start_series(source, a, seconds):
    """(picture, sound) of source a..a+seconds, one value every STEP_S: the mean change between frames of a 96x54 gray
    copy (0-255), and the sound's RMS level in dB (mono, 8 kHz)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{a:.3f}", "-t", f"{seconds:.3f}", "-i", str(source), "-an",
                          "-vf", f"fps={MEASURE_FPS},scale={MEASURE_W}:{MEASURE_H}:flags=area,format=gray", "-f",
                          "rawvideo", "-"], capture_output=True, check=True).stdout
    size = MEASURE_W * MEASURE_H
    shots = [raw[i * size:(i + 1) * size] for i in range(len(raw) // size)]
    diff = [0.0] + [sum(abs(x - y) for x, y in zip(shots[i], shots[i - 1])) / size for i in range(1, len(shots))]
    per = round(MEASURE_FPS * STEP_S)
    picture = [sum(diff[i:i + per]) / per for i in range(0, len(diff) - per + 1, per)]
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{a:.3f}", "-t", f"{seconds:.3f}", "-i", str(source), "-vn",
                          "-ac", "1", "-ar", str(MEASURE_RATE), "-f", "s16le", "-"], capture_output=True, check=True).stdout
    samples = array.array("h")
    samples.frombytes(pcm[:len(pcm) // 2 * 2])
    k = round(MEASURE_RATE * STEP_S)
    sound = [20 * math.log10(max(math.sqrt(sum(v * v for v in samples[i:i + k]) / k), 1.0) / 32768)
             for i in range(0, len(samples) - k + 1, k)]
    return picture, sound


def start_verdict(picture, sound):
    """Rule 2 measured, from start_series' values: (what was measured, problems). The first second's busiest 0.2 s of
    picture change against START_PIC_SHARE of the rest's START_PCT percentile; the loudest STEP_S of the first LINE_BY_S
    against the rest's START_PCT percentile less START_SND_DB. Either one passes (motion by 1.0 s, or a line by 0.5 s)."""
    first, line = round(MOTION_BY_S / STEP_S), round(LINE_BY_S / STEP_S)
    pairs = [(picture[i] + picture[i + 1]) / 2 for i in range(len(picture) - 1)]
    if len(pairs) <= first + 1 or len(sound) <= first + 1:
        return {"note": "too short to measure"}, ["rule 2 (measured): the cut is too short to measure its first second "
                                                  "against the rest"]
    pic_first, pic_busy = max(pairs[:first - 1]), percentile(pairs[first:], START_PCT)
    snd_first, snd_loud = max(sound[:line]), percentile(sound[first:], START_PCT)
    pic_at = (pairs[:first - 1].index(pic_first) + 1) * STEP_S        # the middle of the busiest 0.2 s
    snd_at = sound[:line].index(snd_first) * STEP_S
    pic_need, snd_need = START_PIC_SHARE * pic_busy, snd_loud - START_SND_DB
    moves, heard = pic_first >= pic_need - 1e-9, snd_first >= snd_need - 1e-9
    got = {"picture_first_s": round(pic_first, 2), "picture_rest_busy": round(pic_busy, 2),
           "picture_need": round(pic_need, 2), "sound_first_db": round(snd_first, 1),
           "sound_rest_loud_db": round(snd_loud, 1), "sound_need_db": round(snd_need, 1),
           "picture_at_s": round(pic_at, 2), "sound_at_s": round(snd_at, 2),
           "alive_by": "picture and sound" if moves and heard else "picture" if moves else "sound" if heard else None}
    if moves or heard:
        return got, []
    return got, [f"rule 2 (measured): the first second is still and quiet. Its picture changes {pic_first:.2f} "
                 f"(at least {pic_need:.2f}: {START_PIC_SHARE:g}x the rest's busy level {pic_busy:.2f}), and its first "
                 f"{LINE_BY_S:g} s of sound peaks at {snd_first:.1f} dB (at least {snd_need:.1f}: within "
                 f"{START_SND_DB:g} dB of the rest's loud level {snd_loud:.1f}). Start later, where the action or the "
                 "first line begins"]


def measure_start(source, a, seconds):
    """Rule 2 measured on source a..a+seconds: (what was measured, problems)."""
    return start_verdict(*start_series(source, a, seconds))


def with_replay(picture, sound, replay_picture, replay_sound):
    """The Short's measurements when a replay follows the main part: the replayed footage's values each last
    1 / REPLAY_SPEED as long (rule 2 compares the first second with the rest of the Short, the replay included)."""
    k = round(1 / REPLAY_SPEED)
    return (list(picture) + [v for v in replay_picture for _ in range(k)],
            list(sound) + [v for v in replay_sound for _ in range(k)])


def short_series(source, spec):
    """(picture, sound) of the Short a spec makes, every STEP_S: the main part, then its replay slowed."""
    a, b = float(spec["from_s"]), float(spec["to_s"])
    picture, sound = start_series(source, a, b - a)
    rep = replay_of(spec)
    if rep:
        r0 = float(spec["punchline_s"]) - float(rep["back_s"])
        picture, sound = with_replay(picture, sound, *start_series(source, r0, float(rep["for_s"]) * REPLAY_SPEED))
    return picture, sound


# --- Safety (kept from the repo, rule 9) ---

RECORDINGS_IN = (ROOT / "input", ROOT / "marked")   # where a recording with no index is looked for (Nov 28)
VIDEO_SUFFIXES = (".webm", ".mp4", ".mkv", ".mov")


def source_and_index(stem, source=None, lib=None, folders=RECORDINGS_IN):
    """(video, index) for a render: the library's index when there is one for this file; else a bare one (Nov 28: the
    day's new recordings are not indexed yet, and `render` refused them all), which render() flags NOT INDEXED and
    checks with the safety question alone, as marked mode does. The video: `source`, else the index's, else the file
    named `stem` in the input or marked folder. MemeError when there is no such file."""
    lib = Path(lib or index.LIBRARY)
    idx = index.read_json(lib / stem / "index.json")
    if idx and (source is None or Path(idx["video"]["path"]).resolve() == Path(source).resolve()):
        return Path(source or idx["video"]["path"]), idx
    if source is None:
        found = [Path(d) / f"{stem}{ext}" for d in folders for ext in VIDEO_SUFFIXES if (Path(d) / f"{stem}{ext}").is_file()]
        if not found:
            raise MemeError(f"no index for {stem} and no file {stem}.webm in " + " or ".join(str(d) for d in folders))
        source = found[0]
    if not Path(source).is_file():
        raise MemeError(f"no file {source}")
    return Path(source), {"video": {"stem": stem, "path": str(source)},
                          "profile": {"name": "gta6" if "VI_" in stem else "gta5"}, "sheets": [], "segments": None,
                          "every": 6.0}


def load_index(stem):
    p = index.LIBRARY / stem / "index.json"
    if not p.is_file():
        raise MemeError(f"no index for {stem} ({p}); meme mode reads the index, it never makes one")
    idx = json.loads(p.read_text(encoding="utf-8"))
    return idx


def every_of(idx):
    return float(idx.get("every") or (idx.get("settings") or {}).get("every_s") or 6.0)


def used_spans(used):
    """(video, a, b) of every span _used_footage.json holds: posted, rendered or rejected, all left alone here."""
    return ([tuple(s) for e in used.get("shorts", []) for s in e.get("spans", [])]
            + footage_short.rejected_spans(used))


def overlap(video, a, b, spans):
    return [(v, x, y) for v, x, y in spans if v == video and a < y and x < b]


def footage_problems(spec, idx, used, never):
    """Rule 9's footage part: inside clean footage (no cutscene, menu, phone or loading), never used, posted or
    rejected, nothing the bank may never show."""
    stem = idx["video"]["stem"]
    a = float(spec["from_s"])
    rep = replay_of(spec)
    b = max(float(spec["to_s"]), float(spec["punchline_s"]) - float(rep["back_s"]) + float(rep["for_s"]) * REPLAY_SPEED
            if rep else 0.0)
    out = []
    if idx.get("segments") is None:
        pass                    # not indexed (marked mode: a saved clip, a new recording): the safety question alone
    elif not footage_short.clean(idx, a, b):
        out.append(f"rule 9: {library.hms(a)}-{library.hms(b)} is not clean footage in the index (a cutscene, menu, phone "
                   "call or loading screen is in it or within 0.5 s)")
    hit = overlap(stem, a, b, used_spans(used))
    if hit:
        out.append(f"rule 9: footage already used, posted or rejected: {hit[:3]}")
    for n in never:
        if n["video"] == stem and a < n["end_s"] and n["start_s"] < b:
            out.append(f"rule 9: the bank never shows {n['id']}: {'; '.join(n.get('why', []))}")
    return out


def vision_checks(spec, idx, work, log=print):
    """The vision model's safety question over the footage (once a second) and its first 7 s (twice a second), with
    its own cache in the Short's folder (the library's cache is left alone): (problems, flags)."""
    path = idx["video"]["path"]
    game = "GTA 6" if idx.get("profile", {}).get("name") == "gta6" else "GTA V"
    checker = safety.Checker(game, Path(work) / "_safety_cache", log=log)
    rec = {"sheets": idx["sheets"], "every": every_of(idx)}
    a, b = float(spec["from_s"]), float(spec["to_s"])
    sheets = checker.look(path, a, b, safety.EVERY_S, Path(work) / "safety")
    out, flags = [], []
    for x, y, what in safety.found(sheets, safety.BLOCKING):
        out.append(f"rule 9: {what} at {library.hms(x)}-{library.hms(y)}")
    out += [f"rule 9: never shown: {w}" for w in safety.never_shown(rec, sheets, a, b)]
    first = [(a, min(b, a + safety.FIRST_S))]
    out += [f"rule 9: in the first 7 s: {w}" for w in safety.fmt(safety.gore_in(checker, rec, path, first,
                                                                              Path(work) / "safety"))]
    for x, y, _ in safety.found(sheets, ("subtitles",)):
        flags.append(f"SUBTITLES at {library.hms(x)}-{library.hms(y)}: game subtitles are on screen; read them "
                     "(mission dialogue can swear or tell the story)")
    for x, y, _ in safety.found(sheets, ("blood_or_gore",)):
        flags.append(f"BLOOD at {library.hms(x)}-{library.hms(y)} (after the first 7 s): look at it")
    flags = list(dict.fromkeys(flags))
    got = {"calls": checker.calls, "cached": checker.cached, "seconds": round(checker.seconds, 1)}
    if checker.calls:
        checker.unload(checker.model)    # one heavy job at a time: the model leaves memory before ffmpeg and Whisper
    return out, flags, got


def heard_flags(short):
    """Swearing heard in the finished Short (Whisper, .venv-tts): allowed in meme mode, always flagged."""
    import speech
    if not speech.available():
        return {"note": "faster-whisper is not in this Python; listen once"}, ["SOUND: not transcribed; listen once"]
    ws = speech.words(short)
    bad = [(round(t, 2), swear) for t, _, word in ws for swear in safety.profanity(word)]
    flags = [f"SWEARING in the game's sound at {t:.1f} s: \"{w}\" (allowed in meme mode; your call)" for t, w in bad]
    return {"words": len(ws), "text": " ".join(w for _, _, w in ws), "swears": bad}, flags


# --- Render ---

def slug_of(spec):
    base = spec.get("slug") or caption_of(spec)
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:48] or "meme"


def render(spec, out_dir, source=None, checks="all", log=print, idx=None, used=None, never=None, measure_start=None):
    """The finished meme Short in out_dir: short.mp4, captions.txt, title.txt, meme.json. checks: "all" (the footage,
    the vision model and Whisper, as production runs it) or "none" (the made-up videos of the tests). source: the
    video (default: the recording the index names). measure_start: rule 2 measured on the cut (default: with checks
    "all"); a still, quiet first second stops the render before anything is encoded. Returns the log; MemeError when a
    rule or a check stops it."""
    bad = problems(spec)
    if bad:
        raise MemeError("the spec breaks the rules, nothing was rendered:\n  " + "\n  ".join(bad))
    out_dir = Path(out_dir)
    work = out_dir / "work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    flags, safety_log = [], {}
    if checks == "all":
        idx = idx or load_index(spec["recording"])
        source = source or idx["video"]["path"]
    if measure_start if measure_start is not None else checks == "all":
        got, stop = start_verdict(*short_series(Path(source or spec["recording"]), spec))
        safety_log["first_second"] = got
        if stop:
            raise MemeError("the cut fails rule 2 on the footage itself, nothing was rendered:\n  " + "\n  ".join(stop))
    if checks == "all":
        used = used if used is not None else footage_short.load_used(SHORTS)
        never = never if never is not None else read_bank().get("never_used", [])
        stop = footage_problems(spec, idx, used, never)
        if not stop:
            more, vflags, got = vision_checks(spec, idx, work, log)
            safety_log.update(got)
            stop += more
            flags += vflags
        if stop:
            raise MemeError("the footage fails the kept checks, nothing was rendered:\n  " + "\n  ".join(stop))
        if idx.get("segments") is None:
            flags.append("NOT INDEXED: no index labels (cutscene, menu, phone, loading) to check; the vision model's "
                         "safety question looked at every second instead. The bank's never-shown rule could not run "
                         "in full (a child near violence, a body with blood: they read the index's lines), so look "
                         "for a child or a body yourself")
    source = Path(source or spec["recording"])
    info = clip.probe(shutil.which("ffprobe"), source)
    sw, sh = proof.shown_size(info)
    fix = (":in_color_matrix=bt709:in_range=tv:out_color_matrix=bt709:out_range=tv"
           if clip.needs_range_fix(info["video_codec"], info["color_range"]) else "")
    pre = clip.RANGE_FIX_ARGS if fix else []
    (work / "captions.ass").write_text(ass_document(spec, sw, sh), encoding="utf-8")
    shutil.copy(FONT_FILE, work / FONT_FILE.name)
    tl = timeline(spec)
    inputs = [*pre, "-ss", f"{float(spec['from_s']):.3f}", "-t", f"{tl['main_s'] + 0.5:.3f}", "-i", str(source)]
    rep = replay_of(spec)
    if rep:
        r0 = float(spec["punchline_s"]) - float(rep["back_s"])
        inputs += [*pre, "-ss", f"{r0:.3f}", "-t", f"{float(rep['for_s']) * REPLAY_SPEED + 0.5:.3f}", "-i", str(source)]
    encoder, _, final_args = ex.pick_encoder()
    total = frames(tl["duration_s"])
    ex.ffmpeg([*inputs, "-filter_complex", graph(spec, sw, sh, fix), "-map", "[v]", "-frames:v", total, "-r", FPS,
               *final_args, *ex.COLOUR_TAGS, "-an", "picture.mp4", "-map", "[a]", "-c:a", "pcm_s16le", "-ar", ex.RATE,
               "-ac", "2", "game.wav"], cwd=work)
    music = music_of(spec)
    music_file = None
    if music:
        music_file = MUSIC_DIR / music
        if not music_file.is_file():
            raise MemeError(f"no music file {music_file}")
    sound = mix_sound(spec, work / "game.wav", work, music_file)
    short = out_dir / "short.mp4"
    ex.ffmpeg(["-i", work / "picture.mp4", "-i", work / "mix.wav", "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a",
               "aac", "-b:a", "192k", "-ar", ex.RATE, "-ac", "2", "-shortest", "-movflags", "+faststart", short])
    sound["true_peak"] = safety.guard_true_peak(short, log=log)
    measured = measure(short, spec, work, sw, sh)
    if not measured["caption"]["ok"]:
        raise MemeError(f"rule 3: the caption is not where it must be on the finished file: {measured['caption']}")
    if abs(measured["lufs"] - TARGET_LUFS) > LUFS_TOLERANCE or (measured["true_peak_db"] or -99) >= TRUE_PEAK_MAX:
        raise MemeError(f"rule 5: the sound measures {measured['lufs']} LUFS, {measured['true_peak_db']} dBTP")
    if abs(measured["duration_s"] - tl["duration_s"]) > 1.5 / FPS:
        raise MemeError(f"rule 1: the file is {measured['duration_s']} s; the spec says {tl['duration_s']} s")
    if checks == "all":
        got, hflags = heard_flags(short)
        safety_log["heard"] = got
        flags += hflags
    caps = spec["captions"]
    swap = f"   (swaps to \"{spec['swap']['text']}\" after the payoff)" if isinstance(spec.get("swap"), dict) else ""
    (out_dir / "captions.txt").write_text("".join(f"* {c}{swap}\n" if i == spec["star"] else f"  {c}\n"
                                                  for i, c in enumerate(caps)), encoding="utf-8")
    (out_dir / "title.txt").write_text(spec["title"] + "\n", encoding="utf-8")
    run_log = {"mode": "meme", "made": date.today().isoformat(), "short": str(short), "spec": spec, "timeline": tl,
               "encoder": encoder, "sound": sound, "measured": measured, "safety": safety_log, "flags": flags,
               "source": str(source)}
    (out_dir / LOG_NAME).write_text(json.dumps(run_log, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return run_log


# --- Finding moments (read only) ---

FUNNY = (("fight", r"\b(fight\w*|brawl\w*|punch\w*|knocks? (\w+ )?(down|out|over)|tackl\w*|slap\w*|shov\w*|wrestl\w*)\b"),
         ("ragdoll", r"\b(falls?|fell|falling|tumbl\w*|trips?|ragdoll\w*|flips?|rolls? (on|over|down)|thrown|"
                     r"knocked over|lies on (the )?ground)\b"),
         ("animal", r"\b(dog|cat|gator|alligator|boar|bird|horse|cow|pig|deer|snake|shark|raccoon|monkey|animal)s?\b"),
         ("bug", r"\b(stuck|glitch\w*|bug|bugs|clips? through|floating|upside down)\b"),
         ("argument", r"\b(argu\w*|yell\w*|shout\w*|scream\w*|angry|threaten\w*|insult\w*|curs\w*)\b"),
         ("police", r"\b(arrest\w*|handcuff\w*|surrender\w*|hands up|cop|cops|police officer)\b"),
         ("reaction", r"\b(react\w*|flee\w*|runs? away|panic\w*|cower\w*|protest\w*)\b"),
         ("dance", r"\b(danc\w*)\b"))
RANK = {k: n for n, (k, _) in enumerate(FUNNY)}


def find_candidates(indexes, used, never, merge_s=12.0):
    """Funny candidates in the indexes' lines: [{video, at_s, from_s, to_s, kind, lines, score}], best first. A line that
    denies a thing doesn't count (library.positive_text). Left out: sheets in or near a never_use part, and anything
    that touches used, posted or rejected footage or what the bank never shows."""
    spans = used_spans(used)
    out = []
    for video, idx in indexes.items():
        every = every_of(idx)
        hits = []
        for s in idx.get("sheets", []):
            text = s.get("notable") or ""
            kept = library.positive_text(text)
            kinds = [k for k, pat in FUNNY if re.search(pat, kept)]
            a, b = float(s["t"]), float(s["t"]) + every
            if (not kinds or not footage_short.clean(idx, a, b) or overlap(video, a, b, spans)
                    or any(n["video"] == video and a < n["end_s"] and n["start_s"] < b for n in never)):
                continue
            hits.append((a, b, kinds, text))
        for a, b, kinds, text in hits:
            if out and out[-1]["video"] == video and a - out[-1]["to_s"] <= merge_s and out[-1]["kind"] in kinds:
                out[-1]["to_s"] = b
                out[-1]["lines"].append([a, text])
                continue
            out.append({"video": video, "at_s": a, "from_s": a, "to_s": b, "kind": min(kinds, key=RANK.get),
                        "kinds": kinds, "lines": [[a, text]]})
    for c in out:
        c["score"] = round(len(FUNNY) - RANK[c["kind"]] + 0.5 * (len(c["lines"]) - 1) + 0.3 * (len(c["kinds"]) - 1), 2)
    return sorted(out, key=lambda c: (-c["score"], c["video"], c["at_s"]))


def read_bank():
    if not BANK.is_file():
        return {}
    try:
        return json.loads(BANK.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise index.StateError(f"{BANK} can't be read ({e}); meme mode reads it and never writes it") from None


def gta6_indexes():
    out = {}
    for p in sorted(index.LIBRARY.glob("Grand Theft Auto VI_*/index.json")):
        out[p.parent.name] = json.loads(p.read_text(encoding="utf-8"))
    return out


# --- Commands ---

def hear(stem, a, b, out=None):
    """What is said in a..b of a recording (Whisper on that window alone, cut to a WAV first): [(t, word)] in the
    recording's seconds, with the swear words."""
    import speech
    import tempfile
    idx = load_index(stem)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "window.wav"
        ex.ffmpeg(["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", idx["video"]["path"], "-vn", "-ac", "1", "-ar", "16000",
                   "-c:a", "pcm_s16le", wav])
        ws = speech.words(wav)
    got = [{"t": round(a + t0, 2), "end": round(a + t1, 2), "word": w, "swear": safety.profanity(w)} for t0, t1, w in ws]
    if out:
        Path(out).write_text(json.dumps(got, indent=1), encoding="utf-8")
    return got


def main():
    fastff.use()
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Meme Shorts: a funny moment, the game's own sound, one caption.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="funny candidates from the index and bank (read only)")
    f.add_argument("--top", type=int, default=15)
    f.add_argument("--json", help="also write them here")
    h = sub.add_parser("hear", help="the dialogue in a window of a recording (Whisper, .venv-tts)")
    h.add_argument("recording")
    h.add_argument("from_s", type=float)
    h.add_argument("to_s", type=float)
    h.add_argument("--json")
    c = sub.add_parser("check", help="the rules only")
    c.add_argument("spec")
    r = sub.add_parser("render", help="check and render one meme Short")
    r.add_argument("spec")
    r.add_argument("--out", required=True, help="the Short's folder")
    r.add_argument("--source", help="the video file (default: the index's, else the recording in C:\\AI\\gta\\input "
                                    "or C:\\AI\\gta\\marked; a recording with no index is flagged NOT INDEXED)")
    m = sub.add_parser("marked", help="the owner's marked moments (saved clips, marks.txt): cut, caption, render each "
                                      "new one (.venv-tts)")
    m.add_argument("--marks", help="default C:\\AI\\gta\\marks.txt")
    m.add_argument("--clips", help="default C:\\AI\\gta\\marked")
    m.add_argument("--input", help="the full recordings, default C:\\AI\\gta\\input")
    m.add_argument("--out", help="default C:\\AI\\gta\\shorts (a marked-<date> folder in it)")
    args = ap.parse_args()
    try:
        if args.cmd == "marked":
            import meme_marked
            heavy.main_guard("meme_short.py marked")
            meme_marked.main(args)
            return
        if args.cmd == "find":
            got = find_candidates(gta6_indexes(), footage_short.load_used(SHORTS), read_bank().get("never_used", []))
            for n, cand in enumerate(got[:args.top], 1):
                lines = " | ".join(t for _, t in cand["lines"][:3])
                print(f"{n:2d}. {cand['video'][-14:]} {library.hms(cand['from_s'])}-{library.hms(cand['to_s'])} "
                      f"[{cand['kind']}] {lines}")
            if args.json:
                Path(args.json).write_text(json.dumps(got, indent=1), encoding="utf-8")
            return
        if args.cmd == "hear":
            heavy.main_guard(f"meme_short.py hear {args.recording}")
            for w in hear(args.recording, args.from_s, args.to_s, args.json):
                print(f"{w['t']:8.2f} {w['word']}" + ("   <- SWEAR" if w["swear"] else ""))
            return
        try:
            spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise MemeError(f"{args.spec} can't be read as JSON: {e}") from None
        bad = problems(spec)
        if args.cmd == "check" or bad:
            print("\n".join(bad) or "OK: the spec keeps rules 1-8")
            sys.exit(1 if bad else 0)
        heavy.main_guard(f"meme_short.py render {Path(args.spec).name}")
        source, idx = source_and_index(spec["recording"], args.source)
        got = render(spec, args.out, source=source, idx=idx)
        print(f"READY {got['short']}  {got['measured']['duration_s']} s, {got['measured']['lufs']} LUFS, "
              f"{got['measured']['true_peak_db']} dBTP")
        for flag in got["flags"]:
            print(f"  FLAG {flag}")
    except (MemeError, index.StateError, index.IndexError_, safety.SafetyError, ex.ScriptError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
