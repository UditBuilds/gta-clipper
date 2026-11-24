"""Edit engine v2 (PR 26, "story layer"): a Short about ONE moment of gameplay, with slow background cuts under a
pop-in layer that explains what is on screen (docs/reference/gta6videos_breakdown.html, docs/S4_PLAN.md).

  .venv-tts\\Scripts\\python.exe edit2.py script.json --out FOLDER [--caption-style yellow|rsvp]
  .venv-tts\\Scripts\\python.exe edit2.py script.json --plan        make the voice, print the timeline, render nothing

The owner's rules (PR 26), and where each is kept:
  - one moment per Short, in time order: the background is cut from ONE stretch of one recording ("moment": its
    file and window). Each shot starts at or after the end of the shot before it (place_footage); jumping forward
    inside the moment trims a dull part, and nothing ever goes back in time.
  - context first: the first 2 s show the situation before the action ("action_s"), or the plan says it can't.
  - words match picture: every "anchor" (a word and the evidence that shows it, given by launch.py from the
    evidence the line cites) is on screen within ANCHOR_SLACK_S of the word. place_footage picks each shot's start
    to keep them all, or says which word can't be kept.
  - overlays explain: a circle sits on the box its script gives (the thing its line names), followed frame by frame;
    the break is a freeze that SHOWS the key detail (ringed, with a label naming it) or a split-screen, never a joke.

How it is made:
  1. The voice (explainer.py's Kokoro, am_puck): every word's start and end. Sentence gaps are 0.12 s, so no pause
     is over 250 ms; the Short ends END_AFTER_S after the last syllable. The break is BREAK_S of silence after its line.
  2. The background shots: cut where a word starts, about every SHOT_S (SHOT_MIN_S-SHOT_MAX_S), a line's first word
     preferred; the payoff line starts a shot that is not cut for PAYOFF_HOLD_S. Each fills the screen (a 9:16 crop).
  3. The pop-in layer: each pop lands on its word, POP_LEAD_S early, and stays until the next one:
       pip     a rounded card sliding up into the bottom zone: the HUD corner of the footage on screen ("hud":
               "stars", ringed and zoomed), or a clip or picture ("file", "at") with its pill "label" (and "note").
       circle  a red ring on "box" (x, y, w, h in the recording's pixels at recording time "at"), followed frame by
               frame, while the background zooms 1.00 -> 1.18 at it.
       stat    a pill of 1-4 words; badge: a number pill.
     Gaps aim for POP_GAP[1] s, may stretch to POP_GAP[2] s, and every gap over POP_GAP[1] s is logged with why.
  4. The break: "freeze" (the whole 16:9 frame at "at_s" as a card over a blur, the detail ringed and labelled) or
     "split" (the moment on top, another clip or picture below, each labelled).
  5. The end: explainer's silent subscribe pill in the last 2 s, and the loop ending: the last LOOP_TAIL_S plays the
     footage just before the first frame, so the Short runs back into its start.
  6. Captions one word at a time at CAPTION_Y: "yellow" (explainer's, the owner's pick) or "rsvp".
  7. Sound: the voice at -14 LUFS, explainer's ducked music bed and the footage's own sound (both silent in the
     break), a sound effect on every pop, then safety.py's true-peak guard (under -1.0 dBTP).

Writes short.mp4, voice.wav, captions.ass, log.json and sheet.jpg (a frame every 0.5 s) to the output folder.
Needs .venv-tts (Kokoro, numpy) and ffmpeg/ffprobe on PATH, as explainer.py does.
"""

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import explainer as ex
import fastff
from hooks import escape_ass

W, H, FPS = ex.W, ex.H, ex.FPS
CENTRE_X = W // 2

# --- Background ---
SHOT_S, SHOT_MIN_S, SHOT_MAX_S = 4.4, 3.5, 5.0
SNAP_S = 0.7                          # a cut moves at most this far from its even spacing to land on a word
LINE_BONUS_S = 0.4                    # a line's first word counts this much nearer than it is
PAYOFF_HOLD_S = 4.0                   # no cut this long after the payoff line starts
PAYOFF_SHOT_MAX_S = 6.0               # so the payoff's own shot may run longer than SHOT_MAX_S: it is meant to hold
LOOP_TAIL_S = 1.0
END_AFTER_S = 0.15
SETUP_S = 2.0                         # the first this-many seconds show the moment before its action
ANCHOR_SLACK_S = 0.5                  # a named thing is on screen within this of its word (the owner's rule 4)
GRADE = True

# --- Pop-ins ---
POP_KINDS = ("pip", "circle", "stat", "badge")
POP_LEAD_S = 0.08
POP_GAP = (1.5, 2.0, 3.0)             # closest, target, longest (the owner's: up to 3.0 s when nothing meaningful fits)
POP_MAX_S = 2.4                       # a pop with nothing after it stays up this long (a held one: to the next event)
POP_END_GAP_S = 0.06                  # a pop goes this long before the next one arrives
PIP_SIZE, PIP_RADIUS, PIP_BORDER, PIP_MARGIN = (760, 480), 28, 4, 48
PIP_SHADOW = (0, 12, 12, 0.6)         # dx, dy, blur, alpha: 0 12px 24px rgba(0,0,0,.6)
PIP_X, PIP_Y = 540, 1320
PIP_IN_S, PIP_OUT_S = 0.2, 0.15
PIP_RING_DELAY_S = 0.25               # a hud card's ring arrives once the card has landed
HUD_REGION_W = 560                    # a hud card shows this wide a part of the recording round its target
ZOOM_TO, ZOOM_S = 1.18, 1.5
RING_SIZE = 1.25                      # a ring's radius, as a share of its target's half-width
RING_MIN_R, RING_MAX_R = 60, 220      # on screen, px
POP_IN = ((0.0, 0.6), (0.14, 1.1), (0.26, 1.0))     # a ring's scale as it pops in: (seconds, scale)
STAT_Y, STAT_FONT, BADGE_FONT, LABEL_FONT = 1290, 84, 130, 56
STAT_WORDS = 4
# Following a circle's target: a template match on grey frames at TRACK_SCALE size, TRACK_SEARCH px each way per frame
# at that size. Under TRACK_MIN the target is lost and the ring ends; a ring that would last under RING_MIN_S is left
# out (no circle when we aren't sure where the thing is).
TRACK_SCALE, TRACK_SEARCH, TRACK_MIN, RING_MIN_S = 0.5, 22, 0.5, 0.6
EDGE_PX = 60                          # a circle's spot keeps this far inside the screen's sides

# --- Captions ---
CAPTION_STYLES = ("yellow", "rsvp")
DEFAULT_CAPTIONS = "yellow"           # the owner's pick from the S4 samples (Nov 17)
CAPTION_Y = 740
RSVP_CAP_PX, RSVP_OUTLINE, RSVP_SHADOW, RSVP_WIDTH = 104, 4, 4, 940
RSVP_POP = "\\fscx92\\fscy92\\t(0,60,\\fscx100\\fscy100)"
RSVP_LINGER_S = 0.25

# --- The break ---
BREAK_S = 1.5
BREAK_KINDS = ("freeze", "split")
FREEZE_CARD_W, FREEZE_CARD_Y = 1000, 960     # the frozen 16:9 frame's card: its width and middle
FREEZE_LABEL_Y = 1330
SPLIT_LABEL_Y = (880, 1040)                  # the split's two labels, either side of the seam

SCRIPT_KEYS = {"title", "voice", "speed", "caption_style", "music", "game_audio", "game_volume", "end_pill", "profile",
               "moment", "break", "lines"}
LINE_KEYS = {"say", "pops", "payoff", "loop", "anchors"}
POP_KEYS = {"pip": {"kind", "on", "hud", "file", "at", "focus", "label", "note", "hold", "evidence"},
            "circle": {"kind", "on", "box", "at", "evidence"},
            "stat": {"kind", "on", "text", "hold", "evidence", "facts"},
            "badge": {"kind", "on", "text", "hold", "evidence"}}
BREAK_KEYS = {"freeze": {"after_line", "kind", "at_s", "hud", "box", "label", "evidence"},
              "split": {"after_line", "kind", "file", "at", "focus", "label", "bottom_label", "note"}}


class EditError(Exception):
    """The script can't be made as it is; the text lists every problem."""


# ======================================================================================================================
# The script
# ======================================================================================================================

def line_tokens(say):
    """The spoken words of a line, in the order explainer.synthesize() makes them."""
    return [t for s in ex.split_sentences(say) for t in ex.spoken_tokens(s)]


def is_box(value):
    return (isinstance(value, list) and len(value) == 4 and all(isinstance(v, (int, float)) for v in value)
            and value[2] > 0 and value[3] > 0)


def load(source, caption_style=None):
    """The script (a path or a dict), checked. EditError lists everything wrong."""
    if isinstance(source, dict):
        raw, name = source, source.get("title", "script")
    else:
        try:
            raw, name = json.loads(Path(source).read_text(encoding="utf-8")), Path(source).stem
        except (OSError, ValueError) as e:
            raise EditError(f"can't read {source}: {e}") from None
    problems = [f"unknown key {k!r}" for k in raw if k not in SCRIPT_KEYS]
    style = caption_style or raw.get("caption_style", DEFAULT_CAPTIONS)
    if style not in CAPTION_STYLES:
        problems.append(f"caption_style is one of {', '.join(CAPTION_STYLES)}")
    lines = raw.get("lines")
    if not isinstance(lines, list) or not lines:
        raise EditError("the script needs \"lines\"")
    for n, line in enumerate(lines, 1):
        problems += [f"line {n}: unknown key {k!r}" for k in line if k not in LINE_KEYS]
        if not isinstance(line.get("say"), str) or not line_tokens(line["say"]):
            problems.append(f"line {n}: \"say\" is what the voice says")
            continue
        keys = [ex.word_key(t) for t in line_tokens(line["say"])]
        for a in line.get("anchors", []):
            if ex.word_key(str(a.get("word", ""))) not in keys or not all(
                    isinstance(a.get(k), (int, float)) for k in ("from_s", "to_s")):
                problems.append(f"line {n}: an anchor is {{\"word\": a word of the line, \"from_s\", \"to_s\"}}, "
                                f"not {a!r}")
        for p in line.get("pops", []):
            kind = p.get("kind")
            if kind not in POP_KINDS:
                problems.append(f"line {n}: a pop's kind is one of {', '.join(POP_KINDS)}")
                continue
            problems += [f"line {n}: a {kind} pop has no {k!r}" for k in p if k not in POP_KEYS[kind]]
            if ex.word_key(str(p.get("on", ""))) not in keys:
                problems.append(f"line {n}: a pop lands on a word of its line; {p.get('on')!r} isn't one")
            if kind in ("stat", "badge") and not str(p.get("text", "")).strip():
                problems.append(f"line {n}: a {kind} pop needs its \"text\"")
            if kind == "stat" and len(str(p.get("text", "")).split()) > STAT_WORDS:
                problems.append(f"line {n}: a stat pill is 1-{STAT_WORDS} words, not {p.get('text')!r}")
            if kind == "circle" and not (is_box(p.get("box")) and isinstance(p.get("at"), (int, float))):
                problems.append(f"line {n}: a circle needs \"box\": [x, y, w, h] (the recording's pixels) and \"at\" "
                                "(the recording time the box was read at)")
            if kind == "pip" and not (p.get("hud") or p.get("file")):
                problems.append(f"line {n}: a pip shows the HUD (\"hud\": \"stars\") or a clip (\"file\", \"at\")")
    if sum(1 for l in lines if l.get("payoff")) > 1:
        problems.append("only one line is the payoff")
    brk = raw.get("break")
    if brk is not None:
        kind = brk.get("kind", "freeze") if isinstance(brk, dict) else None
        if kind not in BREAK_KINDS:
            problems.append(f"break: \"kind\" is one of {', '.join(BREAK_KINDS)}")
        elif not (isinstance(brk.get("after_line"), int) and 1 <= brk["after_line"] < len(lines)):
            problems.append("break: \"after_line\" is the line it follows (not the last line)")
        else:
            problems += [f"break: a {kind} has no {k!r}" for k in brk if k not in BREAK_KEYS[kind]]
            if not str(brk.get("label", "")).strip():
                problems.append("break: \"label\" names what it shows (no joke text: the owner's rule)")
            if kind == "freeze" and not (isinstance(brk.get("at_s"), (int, float))
                                         and (brk.get("hud") or is_box(brk.get("box")))):
                problems.append("break: a freeze shows the key detail: \"at_s\" (recording time) and \"hud\": \"stars\" "
                                "or \"box\": [x, y, w, h]")
            if kind == "split" and not (brk.get("file") and str(brk.get("bottom_label", "")).strip()):
                problems.append("break: a split needs \"file\" (and \"at\" for a clip) and \"bottom_label\"")
        brk = dict(brk, kind=kind)
    moment = raw.get("moment")
    visual = None
    if not (isinstance(moment, dict) and isinstance(moment.get("window"), list) and len(moment["window"]) == 2
            and moment["window"][1] > moment["window"][0]):
        problems.append("moment: {\"file\": the recording, \"window\": [from_s, to_s], \"focus\": 0-1, \"action_s\": "
                        "when the action happens}")
    else:
        try:
            visual = ex.parse_file(moment.get("file", ""), str(moment["window"][0]),
                                   (moment.get("focus", 0.5), moment.get("focus_y", 0.5)), None, 1.0, Path(""), "moment")
        except ex.ScriptError as e:
            problems.append(str(e))
        else:
            if visual["kind"] != "video":
                problems.append("moment: the footage is a video")
    pill = None
    if raw.get("end_pill"):
        pill = {"text": " ".join(str(raw["end_pill"]).split()), "style": "info", "pos": "bottom", "font": ex.PILL_SIZE,
                "pad": ex.PILL_PAD, "fill": (255, 255, 255), "ink_colour": ex.INK, "border": 0, "shadow": 6,
                "text_border": 0, "round": 0.5, "y": ex.PILL_Y["bottom"]}
    if problems:
        raise EditError("the script needs fixing:\n  " + "\n  ".join(problems))
    return {"title": raw.get("title") or name, "voice": raw.get("voice", ex.DEFAULT_VOICE),
            "speed": float(raw.get("speed", 1.0)), "caption_style": style, "music": raw.get("music", "auto"),
            "game_audio": raw.get("game_audio", True), "game_volume": float(raw.get("game_volume", 1.0)), "pill": pill,
            "profile": raw.get("profile", "gta5"), "moment": {**moment, "visual": visual}, "break": brk, "lines": lines}


# ======================================================================================================================
# The voice and the timeline
# ======================================================================================================================

def kokoro_voice(kokoro=None):
    """The voice maker: explainer.synthesize with Kokoro (loaded once, when first needed)."""
    state = {"kokoro": kokoro}

    def synth(lines, voice, speed):
        if state["kokoro"] is None:
            state["kokoro"] = ex.load_kokoro()
        return ex.synthesize(state["kokoro"], lines, voice, speed)
    return synth


def make_voice(synth, script):
    """(samples, rate, words, word ranges per line, the break's (start, end) or None, sentences timed by word length,
    the voice's longest pause before the break went in). The break is silence put into the voice after its line;
    the words after it move later by BREAK_S."""
    import numpy as np
    lines = [{"say": l["say"], "tags": {}} for l in script["lines"]]
    audio, rate, words, _, splits = synth(lines, script["voice"], script["speed"])
    quiet = ex.longest_quiet(audio, rate)
    ranges, i = [], 0
    for line in script["lines"]:
        n = len(line_tokens(line["say"]))
        ranges.append((i, i + n))
        i += n
    if i != len(words):
        raise EditError(f"the voice has {len(words)} words where the lines have {i}")
    brk = None
    if script["break"]:
        last = ranges[script["break"]["after_line"] - 1][1] - 1
        at = words[last]["end"] + 0.06
        cut = round(at * rate)
        audio = np.concatenate([audio[:cut], np.zeros(round(BREAK_S * rate), audio.dtype), audio[cut:]])
        for w in words[last + 1:]:
            w["start"] += BREAK_S
            w["end"] += BREAK_S
        brk = (round(at, 3), round(at + BREAK_S, 3))
    return audio, rate, words, ranges, brk, splits, quiet


def snap_cut(target, lo, hi, starts, preferred, avoid=()):
    """The word start nearest target inside (lo, hi) and SNAP_S of it, a preferred one (a line's first word) counting
    LINE_BONUS_S nearer; target itself (a cut mid-word) if no word starts there. avoid: spans no cut may fall in."""
    allowed = [t for t in starts if lo <= t <= hi and not any(a < t < b for a, b in avoid)]
    ok = [t for t in allowed if abs(t - target) <= SNAP_S]
    if not ok:                                   # no word near: the nearest allowed word start, else the target
        return min(allowed, key=lambda t: abs(t - target)) if allowed else target
    return min(ok, key=lambda t: abs(t - target) - (LINE_BONUS_S if t in preferred else 0.0))


def shot_spans(words, ranges, total_s, brk=None, payoff_line=None):
    """The background shots as (start, end, kind) in seconds: "footage", "break" and "loop" (the loop ending). The
    footage between them is cut into shots of about SHOT_S at word starts."""
    loop_at = total_s - LOOP_TAIL_S
    edges = [(0.0, brk[0]), (brk[1], loop_at)] if brk else [(0.0, loop_at)]
    starts = sorted({round(w["start"], 3) for w in words})
    preferred = {round(words[a]["start"], 3) for a, _ in ranges}
    avoid = []
    if payoff_line is not None:
        p = round(words[ranges[payoff_line][0]]["start"], 3)
        preferred.add(p)
        avoid.append((p, p + PAYOFF_HOLD_S))
        # The payoff starts a shot of its own when there is room for a shot before it: a cut on its first word.
        for k, (a, b) in enumerate(edges):
            if a + SHOT_MIN_S * 0.8 <= p <= b - 1.0:
                edges[k:k + 1] = [(a, p), (p, b)]
                break
    spans = []
    for a, b in edges:
        length = b - a
        if length <= 1e-6:
            continue
        n = max(1, round(length / SHOT_S))
        while n > 1 and length / n < SHOT_MIN_S:
            n -= 1
        while length / n > SHOT_MAX_S:
            n += 1
        while True:                              # fewer cuts when one can't land on a word outside the payoff hold
            cuts, prev, clean = [a], a, True
            for j in range(1, n):
                target = a + j * length / n
                c = snap_cut(target, prev + SHOT_MIN_S * 0.9, b - SHOT_MIN_S * 0.9, starts, preferred, avoid)
                clean &= c in starts and not any(x < c < y for x, y in avoid)
                cuts.append(c)
                prev = c
            if clean or n == 1:
                break
            n -= 1
        cuts.append(b)
        spans += [(cuts[k], cuts[k + 1], "footage") for k in range(len(cuts) - 1)]
        if brk and b == brk[0]:
            spans.append((brk[0], brk[1], "break"))
    spans.append((loop_at, total_s, "loop"))
    return [(round(a, 3), round(b, 3), k) for a, b, k in spans if b - a > 1e-6]


def frames_of(spans):
    """Whole frames for each span, from absolute times so rounding never drifts."""
    return [(round(a * FPS), round(b * FPS)) for a, b, _ in spans]


def anchor_words(script, words, ranges):
    """Each anchor with its word's place in the voice: [{line, word, start, end, from_s, to_s, evidence}]."""
    out = []
    for n, line in enumerate(script["lines"]):
        a, b = ranges[n]
        taken = set()
        for anchor in line.get("anchors", []):
            key = ex.word_key(anchor["word"])
            i = next((i for i in range(a, b) if words[i]["key"] == key and i not in taken), None)
            if i is None:
                continue
            taken.add(i)
            out.append({"line": n + 1, "word": words[i]["text"], "start": words[i]["start"], "end": words[i]["end"],
                        "from_s": float(anchor["from_s"]), "to_s": float(anchor["to_s"]),
                        "evidence": anchor.get("evidence")})
    return out


def place_footage(script, spans, anchors):
    """Each shot's footage from the one moment, in time order: a footage shot starts as early as it can, at or after
    the end of the shot before it (so the moment plays on, or skips ahead, never back), and late enough that every
    anchor in it is on screen within ANCHOR_SLACK_S of its word. The break's freeze shows its own frame (something
    already on screen or just about to be); the shot after it carries on. The loop ending plays the footage just
    before the first frame. Returns (shots, problems)."""
    moment = script["moment"]
    v = moment["visual"]
    w0, w1 = moment["window"]
    frames = frames_of(spans)
    shots, problems, prev_end = [], [], w0
    brk = script["break"]
    for k, (a, b, kind) in enumerate(spans):
        f0, f1 = frames[k]
        shot = {"kind": kind, "start": a, "end": b, "f0": f0, "frames": f1 - f0, "visual": v}
        length = shot["frames"] / FPS
        if kind == "footage":
            lo, hi = prev_end, w1 - length
            if lo > hi + 1e-6:                 # the moment has run out: say so, and keep the time order
                over = lo - hi
                problems.append(f"the Short needs {over:.1f} s more footage than its moment has ({w0:.1f}-{w1:.1f} s); "
                                f"cut about {math.ceil(over * 3.8)} words, or say less before the action")
                hi = lo
            here = [x for x in anchors if a - 1e-6 <= x["start"] < b]
            for x in here:                     # on screen at the word's onset, within ANCHOR_SLACK_S
                lo = max(lo, x["from_s"] - ANCHOR_SLACK_S - (x["start"] - a))
                hi = min(hi, x["to_s"] + ANCHOR_SLACK_S - (x["start"] - a))
            if lo > hi + 1e-6:
                for x in here:
                    problems.append(f"line {x['line']}: \"{x['word'].lower()}\" ({a + (x['start'] - a):.1f} s) can't be on "
                                    f"screen with its evidence {x.get('evidence') or ''} ({x['from_s']:.1f}-{x['to_s']:.1f} s "
                                    f"of the recording) while the footage keeps its time order; reorder the lines or "
                                    "cite what is on screen then")
                lo = prev_end                  # keep the time order; the problem above names the words
            shot["src"] = round(max(lo, w0), 3)
            prev_end = shot["src"] + length
        elif kind == "break":
            at = brk.get("at_s", prev_end - 1 / FPS) if brk["kind"] == "freeze" else prev_end
            if brk["kind"] == "freeze" and not w0 <= at <= prev_end + ANCHOR_SLACK_S:
                problems.append(f"break: the freeze shows {at:.1f} s of the recording, which is not what was just on "
                                f"screen ({w0:.1f}-{prev_end:.1f} s)")
            shot["src"] = round(at, 3)
            prev_end = max(prev_end, at)
        else:
            first = shots[0]["src"]
            shot["src"] = round(max(0.0, first - length), 3)
        shots.append(shot)
    if prev_end > w1 + 1e-3 and not any("more footage than its moment has" in p for p in problems):
        problems.append(f"the Short needs footage to {prev_end:.1f} s, past its moment's end ({w1:.1f} s); cut words")
    action = moment.get("action_s")
    if isinstance(action, (int, float)) and shots and shots[0]["src"] + SETUP_S > action + 1e-6:
        problems.append(f"the first {SETUP_S:g} s must show the situation before the action ({action:.1f} s of the "
                        f"recording), but the Short opens at {shots[0]['src']:.1f} s; open on the setup")
    for x in anchors:
        shown = source_time(shots, x["start"])
        x["shown_s"] = None if shown is None else round(shown, 2)
        x["ok"] = shown is not None and x["from_s"] - ANCHOR_SLACK_S - 1e-6 <= shown <= x["to_s"] + ANCHOR_SLACK_S + 1e-6
        if not x["ok"] and not any(f"\"{x['word'].lower()}\"" in p for p in problems):
            problems.append(f"line {x['line']}: \"{x['word'].lower()}\" is said at {x['start']:.1f} s, when "
                            + (f"the recording is at {shown:.1f} s" if shown is not None else "the break is up")
                            + f", not within {ANCHOR_SLACK_S:g} s of its evidence ({x['from_s']:.1f}-{x['to_s']:.1f} s)")
    return shots, problems


def shot_at(shots, t):
    return next((s for s in shots if s["start"] <= t < s["end"]), shots[-1])


def source_time(shots, t):
    """The recording time on screen at Short time t (None in the break)."""
    s = shot_at(shots, t)
    return None if s["kind"] == "break" else s["src"] + (t - s["start"])


def place_pops(script, words, ranges, shots, total_s, brk, pill_from):
    """Each pop's start (its word's onset minus POP_LEAD_S) and end (just before the next event, or POP_MAX_S, or
    the end of its shot for a circle, never into the break or the subscribe pill). Pops closer than POP_GAP[0] are a
    problem for the script's writer. Returns (pops, problems)."""
    pops, problems = [], []
    for n, line in enumerate(script["lines"]):
        a, b = ranges[n]
        for p in line.get("pops", []):
            i = next(i for i in range(a, b) if words[i]["key"] == ex.word_key(p["on"]))
            start = max(0.0, words[i]["start"] - POP_LEAD_S)
            pops.append({**p, "line": n + 1, "word": words[i]["text"], "start": round(start, 3)})
    pops.sort(key=lambda p: p["start"])
    stops = sorted([s["start"] for s in shots if s["kind"] in ("break", "loop")] + [pill_from])
    for k, p in enumerate(pops):
        if brk and brk[0] - 0.05 <= p["start"] < brk[1]:
            problems.append(f"line {p['line']}: the {p['kind']} on {p['word']!r} lands in the break")
        if k and p["start"] - pops[k - 1]["start"] < POP_GAP[0] - 1e-6:
            problems.append(f"line {p['line']}: the {p['kind']} on {p['word']!r} comes {p['start'] - pops[k - 1]['start']:.2f} s "
                            f"after the one before it; pops keep {POP_GAP[0]:g} s apart")
        nxt = pops[k + 1]["start"] if k + 1 < len(pops) else total_s
        stop = min([t for t in stops if t > p["start"] + 0.2] + [nxt])
        end = min(nxt, stop) - POP_END_GAP_S
        if not p.get("hold"):
            end = min(end, p["start"] + POP_MAX_S)
        if p["kind"] == "circle":
            end = min(end, shot_at(shots, p["start"])["end"])
        p["end"] = round(end, 3)
        if end - p["start"] < 0.6:
            problems.append(f"line {p['line']}: the {p['kind']} on {p['word']!r} would be up only {end - p['start']:.2f} s")
    return pops, problems


def gap_report(pops, total_s, brk, payoff_span, pill_from):
    """Every stretch longer than POP_GAP[1] with no new pop, and why: the owner wants each one logged."""
    events = [0.0] + [p["start"] for p in pops] + [pill_from]
    gaps = []
    for a, b in zip(events, events[1:]):
        if b - a <= POP_GAP[1] + 1e-6:
            continue
        why = []
        if brk and a < brk[1] and brk[0] < b:
            why.append("the break")
        if payoff_span and a < payoff_span[1] and payoff_span[0] < b:
            why.append("the payoff hold")
        if not why:
            why.append("no meaningful pop fits here" if b - a <= POP_GAP[2] + 1e-6 else
                       f"LONGER THAN {POP_GAP[2]:g} S: add a pop")
        gaps.append({"from_s": round(a, 2), "to_s": round(b, 2), "seconds": round(b - a, 2), "why": "; ".join(why)})
    return gaps


def plan(script, synth=None):
    """Everything but the pictures: the voice, the shots, the pops, the gaps and every problem found. synth makes the
    voice (default Kokoro); the tests give a stand-in."""
    audio, rate, words, ranges, brk, splits, quiet = make_voice(synth or kokoro_voice(), script)
    total_frames = round((words[-1]["end"] + END_AFTER_S) * FPS)
    total_s = total_frames / FPS
    payoff = next((n for n, l in enumerate(script["lines"]) if l.get("payoff")), None)
    spans = shot_spans(words, ranges, total_s, brk, payoff)
    anchors = anchor_words(script, words, ranges)
    shots, problems = place_footage(script, spans, anchors)
    pill_from = ex.pill_frames(total_frames)[0] / FPS if script["pill"] else total_s
    pops, more = place_pops(script, words, ranges, shots, total_s, brk, pill_from)
    problems += more
    payoff_span = None
    if payoff is not None:
        p = words[ranges[payoff][0]]["start"]
        payoff_span = (round(p, 3), round(min(total_s, p + PAYOFF_HOLD_S), 3))
    gaps = gap_report(pops, total_s, brk, payoff_span, pill_from)
    line_spans = []
    for a, b in ranges:
        t0, t1 = words[a]["start"], words[b - 1]["end"]
        shown = [x for x in (source_time(shots, t0), source_time(shots, t1)) if x is not None]
        line_spans.append((round(min(shown), 2), round(max(shown), 2)) if shown else None)
    return {"audio": audio, "rate": rate, "words": words, "ranges": ranges, "brk": brk, "splits": splits, "quiet": quiet,
            "total_frames": total_frames, "total_s": total_s, "shots": shots, "pops": pops, "gaps": gaps,
            "anchors": anchors, "problems": problems, "payoff_span": payoff_span, "pill_from": pill_from,
            "line_spans": line_spans, "line_starts": [round(words[a]["start"], 3) for a, _ in ranges]}


def print_plan(tl, out=print):
    out(f"Length {tl['total_s']:.2f} s, {len(tl['words'])} words")
    for n, (a, b) in enumerate(tl["ranges"], 1):
        out(f"  line {n}: " + " ".join(f"{w['text']}@{w['start']:.2f}" for w in tl["words"][a:b]))
    for s in tl["shots"]:
        out(f"  {s['start']:6.2f}-{s['end']:6.2f}  {s['kind']:8s} {s['frames'] / FPS:4.2f} s  recording "
            f"{s['src']:.2f}-{s['src'] + s['frames'] / FPS:.2f} s")
    for x in tl["anchors"]:
        out(f"  anchor line {x['line']} {x['word']!r} at {x['start']:.2f} s shows {x['shown_s']} "
            f"(evidence {x['from_s']:.1f}-{x['to_s']:.1f}): {'ok' if x['ok'] else 'NOT ON SCREEN'}")
    for p in tl["pops"]:
        t = source_time(tl["shots"], p["start"])
        out(f"  pop {p['start']:6.2f}-{p['end']:6.2f}  {p['kind']:6s} on {p['word']!r} (line {p['line']})"
            + (f"  recording {t:.2f}" if t is not None else ""))
    for g in tl["gaps"]:
        out(f"  gap {g['from_s']:.2f}-{g['to_s']:.2f} ({g['seconds']:.2f} s): {g['why']}")
    for p in tl["problems"]:
        out(f"  PROBLEM: {p}")


# ======================================================================================================================
# Where things are on screen
# ======================================================================================================================

def crop_of(visual, focus=None):
    """The recording's 9:16 part a full shot shows: x, y, width, height in the recording's pixels."""
    return ex.crop_box({"size": visual["size"], "focus": visual["focus"] if focus is None else focus,
                        "focus_y": visual.get("focus_y", 0.5)})[:4]


def to_screen(visual, x, y, focus=None):
    cx, cy, cw, ch = crop_of(visual, focus)
    return (x - cx) * W / cw, (y - cy) * H / ch


def track_at(track, t):
    """A target's place at recording time t, between the times it was found at."""
    pts = sorted(track)
    if t <= pts[0][0]:
        return pts[0][1], pts[0][2]
    for (t0, x0, y0), (t1, x1, y1) in zip(pts, pts[1:]):
        if t <= t1:
            u = (t - t0) / max(t1 - t0, 1e-6)
            return x0 + (x1 - x0) * u, y0 + (y1 - y0) * u
    return pts[-1][1], pts[-1][2]


def zoom_at(t, t0):
    """The zoom a circle or a hud card gives, t seconds into the Short, its zoom starting at t0: 1 to ZOOM_TO over
    ZOOM_S, easing out."""
    u = min(max((t - t0) / ZOOM_S, 0.0), 1.0)
    return 1 + (ZOOM_TO - 1) * (1 - (1 - u) ** 2)


def zoom_expr(f0):
    """zoom_at() as zoompan's sum, f0 the frame (of the shot or card) its zoom starts on."""
    return f"1+{ZOOM_TO - 1:.4f}*(1-pow(1-clip((on-{f0})/{round(ZOOM_S * FPS)},0,1),2))"


def pop_scale(t):
    """A ring's scale t seconds after it pops in: overshoot, then settle (as explainer's label pop)."""
    for (t0, s0), (t1, s1) in zip(POP_IN, POP_IN[1:]):
        if t < t1:
            return s0 + (s1 - s0) * (t - t0) / (t1 - t0)
    return POP_IN[-1][1]


def profile_file(profile):
    p = Path(profile)
    return p if p.suffix == ".json" else ex.HERE / "profiles" / f"{profile}.json"


def hud_box(profile, name):
    """A HUD element's box [x, y, w, h] in the recording's own pixels (1920x1080), from the HUD profile (a name in
    profiles/ or a path to its .json)."""
    data = json.loads(profile_file(profile).read_text(encoding="utf-8"))
    if name == "stars" and data.get("stars", {}).get("on"):
        return data["stars"]["box"]
    raise EditError(f"the {profile} profile has no {name} box")


def hud_region(box, size):
    """The part of the recording a hud card shows: HUD_REGION_W wide in the card's shape, centred on the box as far as
    the picture allows. (x, y, w, h)."""
    iw, ih = size
    cw, ch = PIP_SIZE[0] - 2 * PIP_BORDER, PIP_SIZE[1] - 2 * PIP_BORDER
    rw = HUD_REGION_W
    rh = round(rw * ch / cw)
    x = min(max(round(box[0] + box[2] / 2 - rw / 2), 0), iw - rw)
    y = min(max(round(box[1] + box[3] / 2 - rh / 2), 0), ih - rh)
    return x, y, rw, rh


def follow(path, t0, seconds, x, y, r, size):
    """Where a target is, frame by frame, from (x, y) at recording time t0 for `seconds`: a template match (normalised
    cross-correlation) on grey frames at TRACK_SCALE size, searched TRACK_SEARCH px round its last place, its template
    refreshed a little each frame. Returns [(time, x, y, match)] in the recording's pixels; the match falls when the
    target is lost (hidden, off screen, or too unlike itself)."""
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view
    iw, ih = size
    w, h = round(iw * TRACK_SCALE), round(ih * TRACK_SCALE)
    done = subprocess.run([ex.FFMPEG, "-v", "error", *ex.source_args(path), "-ss", f"{t0:.3f}", "-t", f"{seconds:.3f}",
                           "-i", str(path), "-vf", f"fps={FPS},scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                          capture_output=True)
    n = len(done.stdout) // (w * h)
    if n == 0:
        return []
    frames = np.frombuffer(done.stdout[:n * w * h], np.uint8).reshape(n, h, w).astype(np.float32)
    half = max(8, round(r * TRACK_SCALE * 0.9))
    cx, cy = x * TRACK_SCALE, y * TRACK_SCALE

    def patch(frame, px, py):
        a, b = int(round(py)) - half, int(round(px)) - half
        if a < 0 or b < 0 or a + 2 * half + 1 > h or b + 2 * half + 1 > w:
            return None
        return frame[a:a + 2 * half + 1, b:b + 2 * half + 1]
    template = patch(frames[0], cx, cy)
    if template is None:
        return []
    out = [(t0, x, y, 1.0)]
    for i in range(1, n):
        s = TRACK_SEARCH
        y0, x0 = int(round(cy)) - half - s, int(round(cx)) - half - s
        if y0 < 0 or x0 < 0 or y0 + 2 * (half + s) + 1 > h or x0 + 2 * (half + s) + 1 > w:
            break                                        # the target is at the picture's edge: stop following it
        region = frames[i][y0:y0 + 2 * (half + s) + 1, x0:x0 + 2 * (half + s) + 1]
        windows = sliding_window_view(region, template.shape)
        tpl = template - template.mean()
        centred = windows - windows.mean(axis=(2, 3), keepdims=True)
        score = (centred * tpl).sum(axis=(2, 3)) / (np.sqrt((centred ** 2).sum(axis=(2, 3)) * (tpl ** 2).sum()) + 1e-6)
        dy, dx = np.unravel_index(int(np.argmax(score)), score.shape)
        cx, cy = x0 + dx + half, y0 + dy + half
        match = float(score[dy, dx])
        out.append((t0 + i / FPS, cx / TRACK_SCALE, cy / TRACK_SCALE, round(match, 3)))
        if match < TRACK_MIN:
            break
        template = 0.8 * template + 0.2 * patch(frames[i], cx, cy)
    return out


def shot_focus(shot, x):
    """The focus a shot needs so x (the recording's pixels) sits inside its 9:16 crop with room: the moment's own
    focus if it already does, else the nearest one that does."""
    v = shot["visual"]
    iw = v["size"][0]
    focus = shot.get("focus", v["focus"])
    cx, _, cw, _ = crop_of(v, focus)
    margin = EDGE_PX * cw / W
    if cx + margin <= x <= cx + cw - margin:
        return focus
    return min(max(x / iw, (cw / 2) / iw), 1 - (cw / 2) / iw)


def circle_points(pop, shot, follow_fn=follow):
    """A background circle's ring: where its target is on screen at Short time t (followed frame by frame from the
    box the script gives), moved by the zoom about the spot the zoom keeps still, and its radius. Also returns that
    spot, the Short time the target was lost at (the ring ends there) or None, and the path followed."""
    v = shot["visual"]
    focus = shot.get("focus", v["focus"])
    src0 = shot["src"] + pop["start"] - shot["start"]
    bx, by, bw, bh = pop["box"]
    t_k, x_k, y_k = pop["at"], bx + bw / 2, by + bh / 2
    r = max(bw, bh) / 2
    seconds = shot["src"] + (pop["end"] - shot["start"]) - min(t_k, src0) + 0.1
    path = follow_fn(v["path"], min(t_k, src0), seconds, x_k, y_k, r, v["size"])
    good = [(t, x, y) for t, x, y, m in path if m >= TRACK_MIN] or [(t_k, x_k, y_k)]
    lost = None
    if len(good) < len(path) or not path or path[-1][0] < shot["src"] + (pop["end"] - shot["start"]) - 0.05:
        lost = round(shot["start"] + good[-1][0] - shot["src"], 3)
    track = [[t, x, y] for t, x, y in good]
    fixed = to_screen(v, *track_at(track, src0), focus)
    radius = min(max(r * W / crop_of(v, focus)[2] * RING_SIZE, RING_MIN_R), RING_MAX_R)

    def points(t):
        x, y = to_screen(v, *track_at(track, shot["src"] + t - shot["start"]), focus)
        z = zoom_at(t, pop["start"])
        return fixed[0] + (x - fixed[0]) * z, fixed[1] + (y - fixed[1]) * z, radius * z
    return points, fixed, lost, path


def pip_geometry(pop, shots, profile):
    """A pip's source (recording, time, region of it) and, for a hud card, its ring's place in the card."""
    content = (PIP_SIZE[0] - 2 * PIP_BORDER, PIP_SIZE[1] - 2 * PIP_BORDER)
    if pop.get("hud"):
        shot = shot_at(shots, pop["start"])
        v = shot["visual"]
        box = hud_box(profile, pop["hud"])
        region = hud_region(box, v["size"])
        tx = (box[0] + box[2] / 2 - region[0]) / region[2]
        ty = (box[1] + box[3] / 2 - region[1]) / region[3]
        r = box[2] / 2 * RING_SIZE * content[0] / region[2]
        src = shot["src"] + pop["start"] - shot["start"] if shot["kind"] != "break" else shot["src"]
        return {"path": v["path"], "src": src, "region": region, "image": False, "target": (tx, ty), "r": r}
    return clip_geometry(pop["file"], pop.get("at", 0), pop.get("focus", 0.5), content, f"pip on {pop['word']}")


def clip_geometry(file, at, focus, size, where):
    """Another clip or picture shown in a box of `size`: its region cut to that shape around its focus."""
    is_video = Path(file).suffix.lower() in ex.VIDEO_EXTS
    v = ex.parse_file(file, str(at) if is_video else None, (focus, 0.5), None, 1.0, Path(""), where)
    iw, ih = v["size"]
    aspect = size[0] / size[1]
    rw, rh = (round(ih * aspect), ih) if iw / ih > aspect else (iw, round(iw / aspect))
    x = min(max(round(v["focus"] * iw - rw / 2), 0), iw - rw)
    return {"path": v["path"], "src": v.get("start", 0.0), "region": (x, (ih - rh) // 2, rw, rh),
            "image": v["kind"] == "image", "target": None, "r": None}


def pip_place():
    """The card's resting place on screen: the content's top-left corner and size."""
    cw, ch = PIP_SIZE[0] - 2 * PIP_BORDER, PIP_SIZE[1] - 2 * PIP_BORDER
    return PIP_X - cw / 2, PIP_Y - ch / 2, cw, ch


def pip_ring_points(pop, geo):
    x0, y0, cw, ch = pip_place()
    t0 = pop["start"] + PIP_RING_DELAY_S
    cx, cy = x0 + geo["target"][0] * cw, y0 + geo["target"][1] * ch

    def points(t):
        return cx, cy, min(max(geo["r"], RING_MIN_R), RING_MAX_R) * zoom_at(t, t0)
    return points


def freeze_card():
    """The frozen frame's card on screen: (x, y, w, h) of the picture inside its border."""
    w = FREEZE_CARD_W - 2 * ex.CARD_BORDER
    h = round(w * 9 / 16 / 2) * 2
    return CENTRE_X - w / 2, FREEZE_CARD_Y - h / 2, w, h


def freeze_target(script, brk, size):
    """Where the freeze's detail is on screen, and its ring's radius."""
    box = hud_box(script["profile"], brk["hud"]) if brk.get("hud") else brk["box"]
    x0, y0, w, h = freeze_card()
    k = w / size[0]
    return x0 + (box[0] + box[2] / 2) * k, y0 + (box[1] + box[3] / 2) * k, max(box[2], box[3]) / 2 * k * RING_SIZE


# ======================================================================================================================
# The overlay layer (ASS)
# ======================================================================================================================

def ring_events(points, start, end):
    """A red ring at each frame's (x, y, radius) from points(t): explainer's circle mark, scaled to the target, popping
    in and fading out. One event a frame, so it can follow a moving target and the zoom."""
    parts = ex.mark_drawing("circle")
    base = ex.MARK_SIZE / 2 * 1.5                        # the drawing's own radius
    events = []
    f_start, f_end = round(start * FPS), round(end * FPS)
    for f in range(f_start, f_end):
        t = f / FPS
        x, y, r = points(t)
        s = 100 * pop_scale(t - start) * r / base
        fade = min(max((f - (f_end - 4)) / 4, 0.0), 1.0)
        edge_a, shadow_a = round(255 * fade), round(0x70 + (255 - 0x70) * fade)
        for fill, edge, border, poly in parts:
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            drawing = "m " + " l ".join(f"{px - min(xs):.1f} {py - min(ys):.1f}" for px, py in poly)
            # Outline only: the fill stays clear (\1a). Only the outline and the shadow fade; an \alpha tag would set
            # the fill's alpha too and paint a solid disc over the target.
            events.append(ex.dialogue(5, ex.frame_time(f), ex.frame_time(f + 1), "Title",
                                      f"{{\\an5\\pos({x:.1f},{y:.1f})\\1a&HFF&\\3c{edge}\\3a&H{edge_a:02X}&\\bord{border}"
                                      f"\\shad3\\4c{ex.BLACK}\\4a&H{shadow_a:02X}&\\fscx{s:.1f}\\fscy{s:.1f}\\p1}}{drawing}"))
    return events


def rsvp_size():
    """The font size that gives Anton capitals RSVP_CAP_PX tall, and the capitals' box at it."""
    probe = 200
    box = ex.measure([(f"\\fn{ex.FONT}\\fs{probe}", "HH")])[0]
    size = round(probe * RSVP_CAP_PX / (box[3] - box[1]))
    return size, ex.measure([(f"\\fn{ex.FONT}\\fs{size}", "HH")])[0]


def rsvp_events(words, total_s, hidden=()):
    """The "rsvp" captions: one word at a time, white ALL CAPS Anton with a black outline and a soft shadow, centred on
    CAPTION_Y, popping from 92 % in 60 ms; a word too wide for RSVP_WIDTH is drawn smaller."""
    size, caps = rsvp_size()
    events = []
    for i, w in enumerate(words):
        following = words[i + 1] if i + 1 < len(words) else None
        end = total_s if following is None else following["start"]
        if following is None or w["pause"] or following["sentence"] != w["sentence"]:
            end = min(end, w["end"] + RSVP_LINGER_S)
        text = escape_ass(w["text"].upper())
        s = size
        limit = RSVP_WIDTH - 2 * (RSVP_OUTLINE + RSVP_SHADOW)
        while ex.centred_width(ex.measure([(f"\\fn{ex.FONT}\\fs{s}", text)])[0]) > limit and s > 40:
            s -= 4
        y = CAPTION_Y - (caps[1] + caps[3]) / 2 * s / size
        tags = (f"{{\\an5\\pos({CENTRE_X},{y:.0f})\\fn{ex.FONT}\\fs{s}\\c{ex.WHITE}\\3c{ex.BLACK}\\bord{RSVP_OUTLINE}"
                f"\\shad{RSVP_SHADOW}\\4c{ex.BLACK}\\4a&H30&\\blur0.6{RSVP_POP}}}")
        events += [ex.dialogue(2, a, b, "Caption", tags + text) for a, b in ex.visible(w["start"], end, hidden)]
    return events


def caption_events(style, words, total_s, hidden):
    if style == "rsvp":
        return rsvp_events(words, total_s, hidden)
    return ex.word_events(words, total_s, hidden, places=[(0.0, total_s + 1.0, CAPTION_Y)])


def label_pill(text, y, style="info", font=LABEL_FONT, x=CENTRE_X):
    label = {"text": " ".join(str(text).split()).upper(), "style": style, "x": x, "y": y, "font": font}
    ex.measure_labels([label])
    if label["size"][0] > W - 160:
        raise EditError(f"the pill {label['text']!r} is too wide; shorten it")
    return label


def pill_events(pop, kind):
    """A stat pill (explainer's label, red) or a number badge (pink), in the bottom zone, popping in."""
    label = label_pill(pop["text"], STAT_Y, "no" if kind == "stat" else "number",
                       STAT_FONT if kind == "stat" else BADGE_FONT)
    if kind == "badge":
        label["round"] = 0.5
    return ex.label_events(label, pop["start"], pop["end"], layers=(6, 7), anim=ex.LABEL_POP + "\\fad(0,120)")


def break_events(script, tl):
    """The break's labels (and the freeze's ring on its detail)."""
    brk, (a, b) = script["break"], tl["brk"]
    events = []
    if brk["kind"] == "freeze":
        x, y, r = freeze_target(script, brk, script["moment"]["visual"]["size"])
        events += ring_events(lambda t: (x, y, min(max(r, RING_MIN_R), RING_MAX_R)), a + 0.15, b)
        events += ex.label_events(label_pill(brk["label"], FREEZE_LABEL_Y, "no", STAT_FONT), a + 0.2, b,
                                  layers=(6, 7))
    else:
        events += ex.label_events(label_pill(brk["label"], SPLIT_LABEL_Y[0]), a + 0.1, b, layers=(6, 7))
        events += ex.label_events(label_pill(brk["bottom_label"], SPLIT_LABEL_Y[1], "no"), a + 0.25, b, layers=(6, 7))
        if brk.get("note"):
            events += ex.label_events(label_pill(brk["note"], H - ex.SAFE_TOP - 40, "grey", 40), a, b, pop=False,
                                      layers=(6, 7))
    return events


# ======================================================================================================================
# Rendering
# ======================================================================================================================

def footage_filter(shot, zoom=None):
    """A footage shot (or the loop ending) filling the screen. With zoom (the frame it starts on, the spot it keeps
    still on screen), the 9:16 part is enlarged twice first and zoompan zooms a frame at a time, as explainer's
    lock-on does."""
    v = shot["visual"]
    focus = shot.get("focus", v["focus"])
    if zoom is None:
        return ex.video_filter(focus, v.get("focus_y", 0.5), GRADE)
    f0, (fx, fy) = zoom
    x, y, cw, ch = crop_of(v, focus)
    return (f"fps={FPS},crop={cw}:{ch}:{x}:{y},scale={W * 2}:{H * 2}:flags=bicubic:{ex.COLOUR_OUT},"
            f"zoompan=z='{zoom_expr(f0)}':x='{fx / W:.5f}*iw*(1-1/zoom)':y='{fy / H:.5f}*ih*(1-1/zoom)':d=1:"
            f"s={W}x{H}:fps={FPS},setsar=1{ex.graded(GRADE)},format=yuv420p,tpad=stop=-1:stop_mode=clone")


def freeze_graph(work):
    """The frozen frame as a white-bordered card (explainer's look C card) over a dark, blurred copy of itself."""
    x0, y0, w, h = freeze_card()
    frame = {"outer": [0, 0, w + 2 * ex.CARD_BORDER, h + 2 * ex.CARD_BORDER],
             "content": [ex.CARD_BORDER, ex.CARD_BORDER, w, h]}
    layer, mask = ex.card_layer(work, frame)
    inset = ex.CARD_MARGIN + ex.CARD_BORDER
    graph = (f"[0:v]split=2[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase:flags=bicubic,crop={W}:{H},"
             f"gblur=sigma=28,eq=brightness=-0.12:saturation=1.2[bg];[b]scale={w}:{h}:flags=lanczos,format=rgba[pic];"
             f"[2:v]format=gray[m];[pic][m]alphamerge[art];[1:v]format=rgba[l];[l][art]overlay={inset}:{inset}:"
             f"format=auto[card];[bg][card]overlay={x0 - inset:.0f}:{y0 - inset:.0f}:format=auto,"
             f"scale={ex.COLOUR_OUT},setsar=1,format=yuv420p")
    return layer, mask, graph


def render_shot(shot, out, encode, work, script, zoom=None):
    v = shot["visual"]
    if shot["kind"] == "break":
        brk = script["break"]
        n = shot["frames"]
        if brk["kind"] == "freeze":
            still = work / f"freeze_{shot['f0']}.png"
            ex.ffmpeg([*ex.source_args(v["path"]), "-ss", f"{shot['src']:.3f}", "-i", v["path"], "-frames:v", 1, still])
            layer, mask, graph = freeze_graph(work)
            loop = ["-loop", 1, "-framerate", FPS]
            ex.ffmpeg([*loop, "-i", still, *loop, "-i", layer, *loop, "-i", mask, "-filter_complex", graph,
                       "-frames:v", n, *encode, *ex.COLOUR_TAGS, out])
            return
        # split: the moment on top (playing on from where it stopped), the other clip or picture below.
        other = clip_geometry(brk["file"], brk.get("at", 0), brk.get("focus", 0.5), (W, H // 2), "break")
        focus = shot.get("focus", v["focus"])
        top = ["-ss", f"{shot['src']:.3f}", "-i", v["path"]]
        bottom = (["-loop", 1, "-framerate", FPS, "-i", other["path"]] if other["image"] else
                  [*ex.source_args(other["path"]), "-ss", f"{other['src']:.3f}", "-i", other["path"]])
        x, y, rw, rh = other["region"]
        graph = (f"[0:v]fps={FPS},scale=-2:{H // 2}:flags=lanczos:{ex.COLOUR_OUT},"
                 f"crop={W}:{H // 2}:x='clip({focus:.4f}*iw-{W // 2},0,iw-{W})':y=0,setsar=1[t];"
                 f"[1:v]fps={FPS},crop={rw}:{rh}:{x}:{y},scale={W}:{H // 2}:flags=lanczos:{ex.COLOUR_OUT},setsar=1[u];"
                 f"[t][u]vstack=2,format=yuv420p,tpad=stop=-1:stop_mode=clone")
        ex.ffmpeg([*ex.source_args(v["path"]), *top, *bottom, "-filter_complex", graph, "-frames:v", n, *encode,
                   *ex.COLOUR_TAGS, out])
        return
    ex.ffmpeg([*ex.source_args(v["path"]), "-ss", f"{shot['src']:.3f}", "-i", v["path"], "-an", "-sn", "-dn",
               "-vf", footage_filter(shot, zoom), "-frames:v", shot["frames"], *encode, *ex.COLOUR_TAGS, out])


def pip_inputs_and_graph(pips, work, base="[0:v]", first_input=2):
    """ffmpeg inputs and filter graph text that lay every pip card over the background: the source through its
    rounded mask onto the card's white layer, zooming at a hud target, padded with clear frames until it starts,
    sliding up into place over PIP_IN_S and away over PIP_OUT_S."""
    frame = {"outer": [0, 0, *PIP_SIZE], "content": [PIP_BORDER, PIP_BORDER, PIP_SIZE[0] - 2 * PIP_BORDER,
                                                   PIP_SIZE[1] - 2 * PIP_BORDER]}
    layer, mask = ex.card_layer(work, frame, radius=PIP_RADIUS, border=PIP_BORDER, shadow=PIP_SHADOW, margin=PIP_MARGIN)
    cw, ch = frame["content"][2:]
    lw, lh = PIP_SIZE[0] + 2 * PIP_MARGIN, PIP_SIZE[1] + 2 * PIP_MARGIN
    inputs, graph, last, i = [], [], base, first_input
    for k, (pop, geo) in enumerate(pips):
        s, e = pop["start"], pop["end"]
        dur = e - s + 0.5
        if geo["image"]:
            inputs += ["-loop", 1, "-framerate", FPS, "-t", f"{dur:.3f}", "-i", geo["path"]]
        else:
            inputs += [*ex.source_args(geo["path"]), "-ss", f"{geo['src']:.3f}", "-t", f"{dur:.3f}", "-i", geo["path"]]
        still = ["-loop", 1, "-framerate", FPS, "-t", f"{dur:.3f}"]
        inputs += [*still, "-i", layer, *still, "-i", mask]
        x, y, rw, rh = geo["region"]
        if geo["target"]:
            f0 = round(PIP_RING_DELAY_S * FPS)
            zoom = (f",scale={cw * 2}:{ch * 2}:flags=bicubic:{ex.COLOUR_OUT},zoompan=z='{zoom_expr(f0)}':"
                    f"x='{geo['target'][0]:.5f}*iw*(1-1/zoom)':y='{geo['target'][1]:.5f}*ih*(1-1/zoom)':d=1:"
                    f"s={cw}x{ch}:fps={FPS}")
        else:
            zoom = f",scale={cw}:{ch}:flags=lanczos:{ex.COLOUR_OUT}"
        rest_y = PIP_Y - lh / 2
        drop = H - rest_y
        slide = (f"{rest_y:.1f}+{drop:.1f}*pow(1-clip((t-{s:.3f})/{PIP_IN_S},0,1),3)"
                 f"+{drop:.1f}*pow(clip((t-{e - PIP_OUT_S:.3f})/{PIP_OUT_S},0,1),2)")
        graph.append(f"[{i}:v]fps={FPS},crop={rw}:{rh}:{x}:{y}{zoom},setsar=1,format=rgba,"
                     f"tpad=stop=-1:stop_mode=clone[p{k}];[{i + 2}:v]format=gray[m{k}];[p{k}][m{k}]alphamerge[a{k}];"
                     f"[{i + 1}:v]format=rgba[l{k}];[l{k}][a{k}]overlay={PIP_MARGIN + PIP_BORDER}:"
                     f"{PIP_MARGIN + PIP_BORDER}:format=auto:shortest=1,"
                     f"tpad=start_duration={s:.3f}:color=black@0[c{k}];"
                     f"{last}[c{k}]overlay=x={PIP_X - lw / 2:.0f}:y='{slide}':eval=frame:format=auto:"
                     f"enable='between(t,{s:.3f},{e:.3f})':eof_action=pass[b{k}]")
        last, i = f"[b{k}]", i + 3
    return inputs, ";".join(graph), last


def mix_sound(voice_wav, music, shots, events, total_frames, brk, game, game_volume, work):
    """The voice, explainer's music bed (cut to silence in the break) and the footage's own sound (none in the
    break), and the sound effects, at -14 LUFS. Returns (the WAV, what went in)."""
    import numpy as np
    n = round(total_frames / FPS * ex.RATE)
    voice = ex.read_wav(voice_wav)
    voice = np.vstack([voice, np.zeros((max(0, n - len(voice)), 2), np.float32)])[:n]
    mix, info = voice.copy(), {}
    quiet = np.ones(n, np.float32)
    if brk:
        a, b = round(brk[0] * ex.RATE), round(brk[1] * ex.RATE)
        ramp = round(0.02 * ex.RATE)
        quiet[a:b] = 0.0
        quiet[max(0, a - ramp):a] = np.linspace(1, 0, a - max(0, a - ramp))
        quiet[b:b + ramp] = np.linspace(0, 1, len(quiet[b:b + ramp]))
    if music:
        bed, info = ex.music_bed(music, voice, 1.0, work)
        mix += bed * quiet[:, None]
    if game:
        slots = [{"visual": {"kind": "video", "sound": True, "path": s["visual"]["path"],
                             "source": f"{Path(s['visual']['path']).stem}@{s['src']:.1f}", "start": s["src"]},
                  "start": s["f0"], "frames": s["frames"], "start_s": s["src"], "line": 0}
                 for s in shots if s["kind"] != "break"]
        bed, game_info = ex.game_bed(slots, voice, game_volume, work)
        if bed is not None:
            mix += bed * quiet[:, None]
            info.update(game_info)
    if events:
        mix += ex.sfx_track(events, n)[:, None]
    raw = work / "mix.f32"
    mix.astype(np.float32).tofile(raw)
    out = work / "mix.wav"
    info["mix_lufs"] = ex.normalise([*ex.F32, "-i", raw], out, ex.measure_loudness(raw, ex.F32)[0])
    return out, info


def sfx_events(pops, brk, pill_click):
    """A sound on every pop (pip: whoosh as it lands, circle: the lock-on click, stat and badge: a hit), a hit on the
    break, and the pill's click."""
    events = []
    for p in pops:
        if p.get("skipped"):
            continue
        if p["kind"] == "pip":
            events.append({"kind": "whoosh", "at_s": p["start"] + PIP_IN_S * 0.6, "why": f"pip on {p['word']}"})
            if p.get("hud"):
                events.append({"kind": "lock", "at_s": p["start"] + PIP_RING_DELAY_S, "why": f"ring on {p['hud']}"})
        elif p["kind"] == "circle":
            events.append({"kind": "lock", "at_s": p["start"], "why": f"circle on {p['word']}"})
        else:
            events.append({"kind": "hit", "at_s": p["start"], "why": f"{p['kind']} {p['text']!r}"})
    if brk:
        events.append({"kind": "hit", "at_s": brk[0], "why": "the break"})
    if pill_click is not None:
        events.append({"kind": "click", "at_s": pill_click, "why": "the cursor clicks the subscribe pill"})
    return sorted(({**e, "at_s": round(e["at_s"], 3)} for e in events), key=lambda e: e["at_s"])


def render_plan(script, tl, out_dir, log=print, follow_fn=follow):
    """Render a planned Short (plan() with no problems) to out_dir. Returns what was made (also in log.json)."""
    started = time.perf_counter()
    if not ex.FFMPEG or not ex.FFPROBE:
        raise EditError("ffmpeg and ffprobe must be on PATH")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    total_frames, total_s, shots, pops, words, brk = (tl[k] for k in ("total_frames", "total_s", "shots", "pops",
                                                                       "words", "brk"))
    held = tl["payoff_span"][0] if tl["payoff_span"] else None
    warnings = [f"background shot {s['start']:.1f}-{s['end']:.1f} s lasts {s['frames'] / FPS:.2f} s, outside "
                f"{SHOT_MIN_S:g}-{SHOT_MAX_S:g}" for s in shots
                if s["kind"] == "footage" and not SHOT_MIN_S - 0.05 <= s["frames"] / FPS <= (
                    PAYOFF_SHOT_MAX_S if held is not None and s["start"] <= held < s["end"] else SHOT_MAX_S) + 0.05]
    timing = {}
    with tempfile.TemporaryDirectory(prefix="work_", dir=out_dir) as work_name:
        work = Path(work_name)
        ex.save_wav(work / "raw.wav", tl["audio"], tl["rate"])
        voice_lufs = ex.normalise_voice(work / "raw.wav", out_dir / "voice.wav")

        # A circle's shot turns to keep its target in the 9:16 crop, before anything is drawn.
        for p in pops:
            if p["kind"] == "circle":
                shot = shot_at(shots, p["start"])
                shot.setdefault("focus", shot_focus(shot, p["box"][0] + p["box"][2] / 2))

        # The overlay layer.
        events = caption_events(script["caption_style"], words, total_s, [brk] if brk else [])
        zooms, pips = {}, []
        for p in pops:
            if p["kind"] == "circle":
                shot = shot_at(shots, p["start"])
                points, fixed, lost, path = circle_points(p, shot, follow_fn)
                p["followed"] = {"frames": len(path), "lowest_match": min((m for *_, m in path), default=None),
                                 "lost_at_s": lost}
                ring_end = p["end"] if lost is None else min(p["end"], lost)
                if not (EDGE_PX <= fixed[0] <= W - EDGE_PX and ex.SAFE_TOP <= fixed[1] <= ex.SAFE_BOTTOM):
                    warnings.append(f"the circle on {p['word']!r} was left out: its target is off the 9:16 picture "
                                    f"({fixed[0]:.0f}, {fixed[1]:.0f})")
                    p["skipped"] = True
                    continue
                if ring_end - p["start"] < RING_MIN_S:
                    warnings.append(f"the circle on {p['word']!r} was left out: its target was lost after "
                                    f"{ring_end - p['start']:.2f} s")
                    p["skipped"] = True
                    continue
                zooms.setdefault(id(shot), (round(p["start"] * FPS) - shot["f0"], fixed))
                events += ring_events(points, p["start"], ring_end)
            elif p["kind"] == "pip":
                geo = pip_geometry(p, shots, script["profile"])
                pips.append((p, geo))
                if geo["target"]:
                    events += ring_events(pip_ring_points(p, geo), p["start"] + PIP_RING_DELAY_S, p["end"] - PIP_OUT_S)
                if p.get("label"):
                    events += ex.label_events(label_pill(p["label"], PIP_Y - PIP_SIZE[1] / 2), p["start"] + PIP_IN_S,
                                              p["end"] - PIP_OUT_S, layers=(6, 7))
                if p.get("note"):
                    events += ex.label_events(label_pill(p["note"], PIP_Y + PIP_SIZE[1] / 2, "grey", 40),
                                              p["start"] + PIP_IN_S, p["end"] - PIP_OUT_S, pop=False, layers=(6, 7))
            else:
                events += pill_events(p, p["kind"])
        if brk:
            events += break_events(script, tl)
        pill_click = None
        if script["pill"]:
            ex.measure_labels([script["pill"]])
            events += ex.end_pill_events(script["pill"], total_frames)
            pill_click = ex.pill_frames(total_frames)[1] / FPS
        (out_dir / "captions.ass").write_text(ex.ass_document(events), encoding="utf-8")
        for font in ex.FONT_FILES:
            shutil.copy(font, work)

        # Sound.
        t = time.perf_counter()
        music, music_note = (ex.pick_music(script["music"], ex.ROOT, total_s, script["title"]) if script["music"]
                             else (None, None))
        sfx = sfx_events(pops, brk, pill_click)
        mix, sound = mix_sound(out_dir / "voice.wav", music, shots, sfx, total_frames, brk, script["game_audio"],
                               script["game_volume"], work)
        timing["sound"] = round(time.perf_counter() - t, 1)

        # Pictures: each background shot, then one pass for the cards, the overlay layer and the sound.
        encoder, piece_args, final_args = ex.pick_encoder()
        t = time.perf_counter()
        names = []
        for k, shot in enumerate(shots):
            names.append(f"seg_{k:03d}.mp4")
            render_shot(shot, work / names[-1], piece_args, work, script, zooms.get(id(shot)))
        timing["shots"] = round(time.perf_counter() - t, 1)
        (work / "segments.txt").write_text("".join(f"file '{n}'\n" for n in names), encoding="utf-8")
        t = time.perf_counter()
        pip_in, pip_graph, last = pip_inputs_and_graph(pips, work)
        graph = (pip_graph + ";" if pip_graph else "") + f"{last}ass=../captions.ass:fontsdir=.,format=yuv420p[v]"
        ex.ffmpeg(["-f", "concat", "-safe", 0, "-i", "segments.txt", "-i", mix, *pip_in, "-filter_complex", graph,
                   "-map", "[v]", "-map", "1:a", "-frames:v", total_frames, "-r", FPS, *final_args, *ex.COLOUR_TAGS,
                   "-c:a", "aac", "-b:a", "192k", "-ar", ex.RATE, "-ac", 2, "-movflags", "+faststart",
                   out_dir / "short.mp4"], cwd=work)
        timing["final"] = round(time.perf_counter() - t, 1)

    import safety
    peak_guard = safety.guard_true_peak(out_dir / "short.mp4", log=lambda m: log("  " + m))
    final_lufs, final_peak = ex.measure_loudness(out_dir / "short.mp4")
    ex.ffmpeg(["-i", out_dir / "short.mp4", "-vf", f"fps=2,scale=216:-2,tile=8x{math.ceil(total_s * 2 / 8)}",
               "-frames:v", 1, "-q:v", 4, out_dir / "sheet.jpg"])
    footage = [s["frames"] / FPS for s in shots if s["kind"] == "footage"]
    shown = [p for p in pops if not p.get("skipped")]
    starts = [p["start"] for p in shown]
    gaps_all = [b - a for a, b in zip(starts, starts[1:])]
    timing["total"] = round(time.perf_counter() - started, 1)
    data = {"title": script["title"], "caption_style": script["caption_style"], "length_s": round(total_s, 3),
            "words": len(words), "encoder": encoder, "longest_voice_pause_s": round(tl["quiet"], 2),
            "background": {"shots": [{"kind": s["kind"], "start_s": s["start"], "end_s": s["end"],
                                      "seconds": round(s["frames"] / FPS, 3),
                                      "recording": Path(s["visual"]["path"]).stem, "from_s": s["src"],
                                      "to_s": round(s["src"] + (s["frames"] / FPS if s["kind"] != "break" else 0), 3),
                                      "focus": round(s.get("focus", s["visual"]["focus"]), 3),
                                      "zoom": bool(zooms.get(id(s)))} for s in shots],
                           "average_s": round(sum(footage) / len(footage), 2) if footage else None},
            "anchors": [{k: x[k] for k in ("line", "word", "start", "shown_s", "from_s", "to_s", "evidence", "ok")}
                        for x in tl["anchors"]],
            "pops": [{k: p[k] for k in ("kind", "start", "end", "word", "line")}
                     | {k: p[k] for k in ("hud", "text", "label", "followed", "skipped") if p.get(k)} for p in pops],
            "pop_interval_s": round(sum(gaps_all) / len(gaps_all), 2) if gaps_all else None,
            "gaps_over_2s": tl["gaps"],
            "break": {"kind": script["break"]["kind"], "from_s": brk[0], "to_s": brk[1], "label": script["break"]["label"],
                      "shows_s": next(s["src"] for s in shots if s["kind"] == "break")} if brk else None,
            "payoff_hold": tl["payoff_span"], "sfx": sfx,
            "loudness": {"voice_lufs": voice_lufs, "mix_lufs": sound.get("mix_lufs"), "final_lufs": final_lufs,
                         "final_true_peak_db": final_peak, "true_peak_guard": peak_guard},
            "music": {"file": str(music) if music else None, "chosen": music_note},
            "game_audio": sound.get("game_pieces"),
            "word_times": [{"text": w["text"], "start_s": round(w["start"], 3), "end_s": round(w["end"], 3)} for w in words],
            "warnings": warnings, "render_s": timing}
    (out_dir / "log.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Rendered {out_dir / 'short.mp4'}: {total_s:.1f} s, {script['caption_style']} captions")
    log(f"  Background: {len(footage)} shots, average {data['background']['average_s']} s; pops: {len(shown)}, "
        f"average interval {data['pop_interval_s']} s")
    for g in tl["gaps"]:
        log(f"  Gap {g['from_s']:.1f}-{g['to_s']:.1f} s ({g['seconds']:.1f} s): {g['why']}")
    log(f"  Loudness {final_lufs:.1f} LUFS, true peak {final_peak:.1f} dB; made in {timing['total']:.0f} s")
    for w in warnings:
        log(f"  WARNING: {w}")
    return data


def render(source, out_dir, caption_style=None, log=print, synth=None):
    """Load, plan and render; EditError if the plan has problems."""
    script = load(source, caption_style)
    tl = plan(script, synth)
    if tl["problems"]:
        raise EditError("the script needs fixing:\n  " + "\n  ".join(tl["problems"]))
    return render_plan(script, tl, out_dir, log)


def main():
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Edit engine v2: one moment, slow background cuts, a pop-in layer.")
    ap.add_argument("script", type=Path)
    ap.add_argument("--out", type=Path, help="the output folder")
    ap.add_argument("--caption-style", choices=CAPTION_STYLES)
    ap.add_argument("--plan", action="store_true", help="make the voice and print the timeline; render nothing")
    args = ap.parse_args()
    fastff.use()
    try:
        if args.plan:
            print_plan(plan(load(args.script, args.caption_style)))
        else:
            if not args.out:
                ap.error("--out FOLDER is needed to render")
            render(args.script, args.out, args.caption_style)
    except (EditError, ex.ScriptError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
