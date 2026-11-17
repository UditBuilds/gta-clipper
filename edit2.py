"""Edit engine v2 (PR 26, "story layer"): a Short from gameplay footage with slow background cuts under a fast pop-in
layer, the way top GTA Shorts channels cut (docs/reference/gta6videos_breakdown.html, docs/S4_PLAN.md).

  .venv-tts\\Scripts\\python.exe edit2.py script.json --out FOLDER [--caption-style yellow|rsvp]
  .venv-tts\\Scripts\\python.exe edit2.py script.json --plan        make the voice, print the timeline, render nothing

What it does, in order:
  1. The voice (explainer.py's Kokoro, am_puck): every word's start and end. Sentence gaps are explainer's 0.12 s, so
     no pause is longer than 250 ms; the Short ends END_AFTER_S after the last syllable. A "break" (the midpoint
     pattern break) is a deliberate BREAK_S of silence in the voice after its line.
  2. The background track: footage cut only where a word starts, about every SHOT_S (SHOT_MIN_S-SHOT_MAX_S), a line's
     first word preferred. The break and the loop ending are cuts of their own. The payoff line starts a shot that is
     not cut for PAYOFF_HOLD_S. Each shot fills the screen (a 9:16 crop around its focus).
  3. The pop-in layer, on top: each pop lands on the word it names ("on"), POP_LEAD_S early so its sound peaks on the
     word, and stays until the next one. Kinds:
       pip     a rounded evidence card (760x480, white border, shadow) sliding up into the bottom zone: the HUD corner
               of the footage on screen ("hud": "stars"), or another clip or picture ("file", "at"), with an optional
               pill label ("GTA 5", "ROCKSTAR" ...). A "hud" card rings its target and zooms 1.00 -> 1.18 at it.
       circle  a red ring on something in the footage ("track": where it is, in the recording's own pixels, at one or
               more times), and the background zooms 1.00 -> 1.18 at it over 1.5 s, keeping that spot still.
       stat    a pill of 1-3 words from the line.
       badge   a number pill (countdowns).
     The owner's rules (PR 26): filler must mean something; gaps aim for POP_GAP[1] s and may stretch to POP_GAP[2] s;
     every gap over POP_GAP[1] s is logged with its reason.
  4. The break: the frame where the footage stops, frozen in black and white with a slow push-in and a line of deadpan
     text on a black bar; the music and the game's sound cut out, a hit lands on it.
  5. The end: explainer's silent subscribe pill in the last 2 s, and the loop ending: the last LOOP_TAIL_S plays the
     footage just before the first frame, so the Short runs back into its start.
  6. Captions, one word at a time at CAPTION_Y (the middle zone; the bottom zone is the pop-ins'): "yellow" is
     explainer's word captions (Lilita One, yellow), "rsvp" is white ALL CAPS Anton with a black outline.
  7. Sound: the voice at -14 LUFS, explainer's ducked music bed and the footage's own sound under it, a sound effect
     on every pop (pip: whoosh, circle: lock-on click, stat/badge: hit, break: hit, pill: click), then the true-peak
     guard (safety.py: under -1.0 dBTP).

Writes short.mp4, voice.wav, captions.ass, log.json and sheet.jpg (a frame every 0.5 s) to the output folder.
Needs .venv-tts (Kokoro, numpy) and ffmpeg/ffprobe on PATH, as explainer.py does.
"""

import argparse
import json
import math
import shutil
import sys
import tempfile
import time
from pathlib import Path

import explainer as ex
from hooks import escape_ass

W, H, FPS = ex.W, ex.H, ex.FPS
CENTRE_X = W // 2

# --- Background ---
SHOT_S, SHOT_MIN_S, SHOT_MAX_S = 4.4, 3.5, 5.0
SNAP_S = 0.7                          # a cut moves at most this far from its even spacing to land on a word
LINE_BONUS_S = 0.4                    # a line's first word counts this much nearer than it is
PAYOFF_HOLD_S = 4.0                   # no cut this long after the payoff line starts
LOOP_TAIL_S = 1.0
END_AFTER_S = 0.15
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
STAT_Y, STAT_FONT, BADGE_FONT = 1290, 84, 130
# Following a circle's target: a template match on grey frames at TRACK_SCALE size, TRACK_SEARCH px each way per frame
# at that size. Under TRACK_MIN the target is lost and the ring ends; a ring that would last under RING_MIN_S is left
# out (the owner's rule: no circle when we aren't sure where the thing is).
TRACK_SCALE, TRACK_SEARCH, TRACK_MIN, RING_MIN_S = 0.5, 22, 0.5, 0.6
FILL_KINDS = ("circle", "stat", "badge")            # what may fill a gap (never an unrelated picture)

# --- Captions ---
CAPTION_STYLES = ("yellow", "rsvp")
CAPTION_Y = 740
RSVP_CAP_PX, RSVP_OUTLINE, RSVP_SHADOW, RSVP_WIDTH = 104, 4, 4, 940
RSVP_POP = "\\fscx92\\fscy92\\t(0,60,\\fscx100\\fscy100)"
RSVP_LINGER_S = 0.25

# --- The break ---
BREAK_S = 1.5
BREAK_PUSH = 0.04                      # the frozen frame's slow push-in
BREAK_BAR_Y, BREAK_BAR_H, BREAK_TEXT_PX = 430, 150, 64      # the bar's top and height; the text's capital height
BREAK_WIDTH = 960

SCRIPT_KEYS = {"title", "voice", "speed", "caption_style", "music", "game_audio", "game_volume", "end_pill", "profile",
               "background", "break", "lines"}
LINE_KEYS = {"say", "pops", "payoff", "loop"}
POP_KEYS = {"pip": {"kind", "on", "hud", "file", "at", "focus", "label", "hold"},
            "circle": {"kind", "on", "track", "r"},
            "stat": {"kind", "on", "text", "style", "hold"},
            "badge": {"kind", "on", "text", "hold"}}


class EditError(Exception):
    """The script can't be made as it is; the text lists every problem."""


# ======================================================================================================================
# The script
# ======================================================================================================================

def line_tokens(say):
    """The spoken words of a line, in the order explainer.synthesize() makes them."""
    return [t for s in ex.split_sentences(say) for t in ex.spoken_tokens(s)]


def load(path, caption_style=None):
    """The script, checked. EditError lists everything wrong."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise EditError(f"can't read {path}: {e}") from None
    problems = [f"unknown key {k!r}" for k in raw if k not in SCRIPT_KEYS]
    style = caption_style or raw.get("caption_style", "yellow")
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
            if kind == "stat":
                words = str(p.get("text", "")).split()
                if len(words) > 3 or any(ex.word_key(w) not in keys for w in words):
                    problems.append(f"line {n}: a stat pill is 1-3 words from its own line, not {p.get('text')!r}")
            if kind == "circle" and not (isinstance(p.get("track"), list) and p["track"]
                                         and all(isinstance(k, list) and len(k) == 3 for k in p["track"])):
                problems.append(f"line {n}: a circle needs \"track\": [[recording time, x, y], ...]")
            if kind == "pip" and not (p.get("hud") or p.get("file")):
                problems.append(f"line {n}: a pip shows the HUD (\"hud\": \"stars\") or a clip (\"file\", \"at\")")
    if sum(1 for l in lines if l.get("payoff")) > 1:
        problems.append("only one line is the payoff")
    brk = raw.get("break")
    if brk is not None:
        if not (isinstance(brk, dict) and isinstance(brk.get("after_line"), int)
                and 1 <= brk["after_line"] < len(lines) and str(brk.get("text", "")).strip()):
            problems.append("break: {\"after_line\": N (not the last line), \"text\": \"a few deadpan words\"}")
    background = raw.get("background")
    if not isinstance(background, list) or not background:
        problems.append("background: a list of footage, one per background shot ({\"file\", \"at\", \"focus\"} or "
                        "{\"continue\": true})")
        background = []
    shots = []
    for k, b in enumerate(background, 1):
        if b.get("continue"):
            if not shots:
                problems.append("background 1 can't continue anything")
                continue
            shots.append({**shots[-1], "continue": True, "focus": b.get("focus", shots[-1]["focus"]),
                          "focus_y": b.get("focus_y", shots[-1]["focus_y"])})
            continue
        try:
            v = ex.parse_file(b.get("file", ""), str(b.get("at", 0)), (b.get("focus", 0.5), b.get("focus_y", 0.5)), None,
                              1.0, Path(""), f"background {k}")
        except ex.ScriptError as e:
            problems.append(str(e))
            continue
        if v["kind"] != "video":
            problems.append(f"background {k}: footage is a video")
            continue
        shots.append({**v, "continue": False})
    pill = None
    if raw.get("end_pill"):
        pill = {"text": " ".join(str(raw["end_pill"]).split()), "style": "info", "pos": "bottom", "font": ex.PILL_SIZE,
                "pad": ex.PILL_PAD, "fill": (255, 255, 255), "ink_colour": ex.INK, "border": 0, "shadow": 6,
                "text_border": 0, "round": 0.5, "y": ex.PILL_Y["bottom"]}
    if problems:
        raise EditError("the script needs fixing:\n  " + "\n  ".join(problems))
    title = raw.get("title") or Path(path).stem
    return {"title": title, "voice": raw.get("voice", ex.DEFAULT_VOICE), "speed": float(raw.get("speed", 1.0)),
            "caption_style": style, "music": raw.get("music", "auto"), "game_audio": raw.get("game_audio", True),
            "game_volume": float(raw.get("game_volume", 1.0)), "pill": pill, "profile": raw.get("profile", "gta5"),
            "background": shots, "break": brk, "lines": lines}


# ======================================================================================================================
# The voice and the timeline
# ======================================================================================================================

def make_voice(kokoro, script):
    """(samples, rate, words, word ranges per line, the break's (start, end) or None, sentences timed by word length,
    the voice's longest pause before the break went in). The break is silence put into
    the voice after its line; the words after it move later by BREAK_S."""
    import numpy as np
    lines = [{"say": l["say"], "tags": {}} for l in script["lines"]]
    audio, rate, words, _, splits = ex.synthesize(kokoro, lines, script["voice"], script["speed"])
    quiet = ex.longest_quiet(audio, rate)                # before the break goes in: the voice's own longest pause
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
    ok = [t for t in starts if lo <= t <= hi and abs(t - target) <= SNAP_S and not any(a < t < b for a, b in avoid)]
    if not ok:
        return target
    return min(ok, key=lambda t: abs(t - target) - (LINE_BONUS_S if t in preferred else 0.0))


def shot_spans(words, ranges, total_s, brk=None, payoff_line=None):
    """The background shots as (start, end, kind) in seconds: "footage", "freeze" (the break) and "loop" (the loop
    ending). The footage between them is cut into shots of about SHOT_S at word starts."""
    loop_at = total_s - LOOP_TAIL_S
    edges = [(0.0, brk[0]), (brk[1], loop_at)] if brk else [(0.0, loop_at)]
    starts = sorted({round(w["start"], 3) for w in words})
    preferred = {round(words[a]["start"], 3) for a, _ in ranges}
    avoid = []
    if payoff_line is not None:
        p = round(words[ranges[payoff_line][0]]["start"], 3)
        preferred.add(p)
        avoid.append((p, p + PAYOFF_HOLD_S))
    spans = []
    for a, b in edges:
        length = b - a
        n = max(1, round(length / SHOT_S))
        while n > 1 and length / n < SHOT_MIN_S:
            n -= 1
        while length / n > SHOT_MAX_S:
            n += 1
        cuts, prev = [a], a
        for j in range(1, n):
            target = a + j * length / n
            c = snap_cut(target, prev + SHOT_MIN_S * 0.9, b - SHOT_MIN_S * 0.9, starts, preferred, avoid)
            cuts.append(c)
            prev = c
        cuts.append(b)
        spans += [(cuts[k], cuts[k + 1], "footage") for k in range(len(cuts) - 1)]
        if brk and b == brk[0]:
            spans.append((brk[0], brk[1], "freeze"))
    spans.append((loop_at, total_s, "loop"))
    return [(round(a, 3), round(b, 3), k) for a, b, k in spans if b - a > 1e-6]


def frames_of(spans):
    """Whole frames for each span, from absolute times so rounding never drifts."""
    return [(round(a * FPS), round(b * FPS)) for a, b, _ in spans]


def assign_footage(script, spans):
    """Each footage shot's recording, start and focus, from the script's background list in order ("continue" carries
    on where the shot before it stopped, after a freeze too); the freeze's frame; the loop ending's footage."""
    frames = frames_of(spans)
    footage = [k for k, s in enumerate(spans) if s[2] == "footage"]
    if len(script["background"]) != len(footage):
        raise EditError(f"the timeline has {len(footage)} background shot(s) ({', '.join(f'{spans[k][0]:.1f}-{spans[k][1]:.1f} s' for k in footage)}) "
                        f"but the script lists {len(script['background'])}")
    shots, src_end = [], None
    bg = iter(script["background"])
    for k, (a, b, kind) in enumerate(spans):
        f0, f1 = frames[k]
        shot = {"kind": kind, "start": a, "end": b, "f0": f0, "frames": f1 - f0}
        if kind == "footage":
            v = next(bg)
            start = src_end if v["continue"] else v["start"]
            shot.update(visual=v, src=round(start, 3))
            src_end = start + shot["frames"] / FPS
        elif kind == "freeze":
            prev = shots[-1]
            shot.update(visual=prev["visual"], src=round(src_end - 1 / FPS, 3))
        else:
            first = shots[0]
            if first["src"] - shot["frames"] / FPS < 0:
                raise EditError("the loop ending plays the footage just before the first frame, and there is none")
            shot.update(visual=first["visual"], src=round(first["src"] - shot["frames"] / FPS, 3))
        shots.append(shot)
    return shots


def shot_at(shots, t):
    return next((s for s in shots if s["start"] <= t < s["end"]), shots[-1])


def source_time(shots, t):
    """The recording time on screen at Short time t (None in the break)."""
    s = shot_at(shots, t)
    return None if s["kind"] == "freeze" else s["src"] + (t - s["start"])


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
    stops = sorted([s["start"] for s in shots if s["kind"] in ("freeze", "loop")] + [pill_from])
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


def gap_report(pops, shots, total_s, brk, payoff_span, pill_from):
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


# ======================================================================================================================
# Where things are on screen
# ======================================================================================================================

def crop_of(visual):
    """The recording's 9:16 part a full shot shows: x, y, width, height in the recording's pixels."""
    return ex.crop_box({"size": visual["size"], "focus": visual["focus"], "focus_y": visual.get("focus_y", 0.5)})[:4]


def to_screen(visual, x, y):
    cx, cy, cw, ch = crop_of(visual)
    return (x - cx) * W / cw, (y - cy) * H / ch


def track_at(track, t):
    """A target's place at recording time t, between the times it was read at."""
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


def hud_box(profile, name):
    """A HUD element's box [x, y, w, h] in the recording's own pixels (1920x1080), from the HUD profile."""
    data = json.loads((ex.HERE / "profiles" / f"{profile}.json").read_text(encoding="utf-8"))
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


def follow(path, t0, seconds, x, y, r, size):
    """Where a target is, frame by frame, from (x, y) at recording time t0 for `seconds`: a template match (normalised
    cross-correlation) on grey frames at half size, searched TRACK_SEARCH px round its last place, its template
    refreshed a little each frame. Returns [(time, x, y, match)] in the recording's pixels; the match falls when the
    target is lost (hidden, off screen, or too unlike itself)."""
    import subprocess
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


def circle_points(pop, shots):
    """A background circle's ring: where its target is on screen at Short time t (followed frame by frame from the
    script's first point), moved by the zoom about the spot the zoom keeps still, and its radius. Also returns that
    spot and the Short time the target was lost at (the ring ends there), or None."""
    shot = shot_at(shots, pop["start"])
    v = shot["visual"]
    src0 = shot["src"] + pop["start"] - shot["start"]
    t_k, x_k, y_k = sorted(pop["track"])[0]
    seconds = shot["src"] + (pop["end"] - shot["start"]) - min(t_k, src0) + 0.1
    path = follow(v["path"], min(t_k, src0), seconds, x_k, y_k, pop.get("r", 60), v["size"])
    good = [(t, x, y) for t, x, y, m in path if m >= TRACK_MIN] or [(t_k, x_k, y_k)]
    lost = None
    if len(good) < len(path) or path and path[-1][0] < shot["src"] + (pop["end"] - shot["start"]) - 0.05:
        lost = round(shot["start"] + good[-1][0] - shot["src"], 3)
    track = [[t, x, y] for t, x, y in good]
    fixed = to_screen(v, *track_at(track, src0))
    radius = min(max(pop.get("r", 60) * W / crop_of(v)[2] * RING_SIZE, RING_MIN_R), RING_MAX_R)

    def points(t):
        x, y = to_screen(v, *track_at(track, shot["src"] + t - shot["start"]))
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
        src = shot["src"] + pop["start"] - shot["start"] if shot["kind"] != "freeze" else shot["src"]
        return {"path": v["path"], "src": src, "region": region, "image": False, "target": (tx, ty), "r": r}
    v = ex.parse_file(pop["file"], str(pop.get("at", 0)) if Path(pop["file"]).suffix.lower() in ex.VIDEO_EXTS else None,
                      (pop.get("focus", 0.5), 0.5), None, 1.0, Path(""), f"pip on {pop['word']}")
    iw, ih = v["size"]
    aspect = content[0] / content[1]
    rw, rh = (round(ih * aspect), ih) if iw / ih > aspect else (iw, round(iw / aspect))
    x = min(max(round(v["focus"] * iw - rw / 2), 0), iw - rw)
    return {"path": v["path"], "src": v.get("start", 0.0), "region": (x, (ih - rh) // 2, rw, rh),
            "image": v["kind"] == "image", "target": None, "r": None}


def pip_place(pop):
    """The card's resting place on screen: the content's top-left corner and size."""
    cw, ch = PIP_SIZE[0] - 2 * PIP_BORDER, PIP_SIZE[1] - 2 * PIP_BORDER
    return PIP_X - cw / 2, PIP_Y - ch / 2, cw, ch


def pip_ring_points(pop, geo):
    x0, y0, cw, ch = pip_place(pop)
    t0 = pop["start"] + PIP_RING_DELAY_S
    cx, cy = x0 + geo["target"][0] * cw, y0 + geo["target"][1] * ch

    def points(t):
        return cx, cy, min(max(geo["r"], RING_MIN_R), RING_MAX_R) * zoom_at(t, t0)
    return points


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


def pill_events(pop, kind):
    """A stat pill (explainer's label, red) or a number badge (pink), in the bottom zone, popping in."""
    label = {"text": str(pop["text"]).upper(), "style": "no" if kind == "stat" else "number", "x": CENTRE_X, "y": STAT_Y,
             "font": STAT_FONT if kind == "stat" else BADGE_FONT}
    if kind == "badge":
        label["round"] = 0.5
    ex.measure_labels([label])
    if label["size"][0] > W - 160:
        raise EditError(f"the {kind} {label['text']!r} is too wide; shorten it")
    return ex.label_events(label, pop["start"], pop["end"], layers=(6, 7), anim=ex.LABEL_POP + "\\fad(0,120)")


def break_events(brk, text):
    """The break's black bar across the top of the safe area with its deadpan line, white Anton, as it is written."""
    probe = 200
    caps = ex.measure([(f"\\fn{ex.FONT}\\fs{probe}", "Hh")])[0]
    size = round(probe * BREAK_TEXT_PX / (caps[3] - caps[1]))
    line = escape_ass(" ".join(str(text).split()))
    while ex.centred_width(ex.measure([(f"\\fn{ex.FONT}\\fs{size}", line)])[0]) > BREAK_WIDTH - 80 and size > 30:
        size -= 2
    box = ex.measure([(f"\\fn{ex.FONT}\\fs{size}", line)])[0]
    y = BREAK_BAR_Y + BREAK_BAR_H / 2 - (box[1] + box[3]) / 2
    bar = f"m 0 0 l {W} 0 l {W} {BREAK_BAR_H} l 0 {BREAK_BAR_H}"
    return [ex.dialogue(8, brk[0], brk[1], "Title", f"{{\\an7\\pos(0,{BREAK_BAR_Y})\\c{ex.BLACK}\\bord0\\shad0\\p1}}{bar}"),
            ex.dialogue(9, brk[0], brk[1], "Title", f"{{\\an5\\pos({CENTRE_X},{y:.0f})\\fn{ex.FONT}\\fs{size}\\c{ex.WHITE}"
                                                    f"\\bord0\\shad0}}{line}")]


# ======================================================================================================================
# Rendering
# ======================================================================================================================

def footage_filter(shot, zoom=None):
    """A footage shot (or the loop ending) filling the screen. With zoom (the frame it starts on, the spot it keeps
    still on screen), the 9:16 part is enlarged twice first and zoompan zooms a frame at a time, as explainer's
    lock-on does."""
    v = shot["visual"]
    grade = ex.graded(GRADE)
    if zoom is None:
        return ex.video_filter(v["focus"], v.get("focus_y", 0.5), GRADE)
    f0, (fx, fy) = zoom
    x, y, cw, ch = crop_of(v)
    return (f"fps={FPS},crop={cw}:{ch}:{x}:{y},scale={W * 2}:{H * 2}:flags=bicubic:{ex.COLOUR_OUT},"
            f"zoompan=z='{zoom_expr(f0)}':x='{fx / W:.5f}*iw*(1-1/zoom)':y='{fy / H:.5f}*ih*(1-1/zoom)':d=1:"
            f"s={W}x{H}:fps={FPS},setsar=1{grade},format=yuv420p,tpad=stop=-1:stop_mode=clone")


def render_shot(shot, out, encode, work, zoom=None):
    v = shot["visual"]
    if shot["kind"] == "freeze":
        still = work / f"freeze_{shot['f0']}.png"
        ex.ffmpeg([*ex.source_args(v["path"]), "-ss", f"{shot['src']:.3f}", "-i", v["path"], "-frames:v", 1,
                   "-vf", ex.video_filter(v["focus"], v.get("focus_y", 0.5), False), still])
        n = shot["frames"]
        graph = (f"scale={W * 2}:{H * 2}:flags=bicubic,zoompan=z='1+{BREAK_PUSH}*on/{max(n - 1, 1)}':"
                 f"x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={W}x{H}:fps={FPS},hue=s=0,eq=contrast=1.15,"
                 f"setsar=1,format=yuv420p")
        ex.ffmpeg(["-loop", 1, "-framerate", FPS, "-i", still, "-vf", graph, "-frames:v", n, *encode,
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
                 for s in shots if s["kind"] != "freeze"]
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


def timeline(script, kokoro):
    """Voice, shots, pops and gaps: everything but the pictures. Returns a dict (see render())."""
    audio, rate, words, ranges, brk, splits, quiet = make_voice(kokoro, script)
    total_frames = round((words[-1]["end"] + END_AFTER_S) * FPS)
    total_s = total_frames / FPS
    payoff = next((n for n, l in enumerate(script["lines"]) if l.get("payoff")), None)
    spans = shot_spans(words, ranges, total_s, brk, payoff)
    shots = assign_footage(script, spans)
    pill_from = ex.pill_frames(total_frames)[0] / FPS if script["pill"] else total_s
    pops, problems = place_pops(script, words, ranges, shots, total_s, brk, pill_from)
    payoff_span = None
    if payoff is not None:
        p = words[ranges[payoff][0]]["start"]
        payoff_span = (p, min(total_s, p + PAYOFF_HOLD_S))
    gaps = gap_report(pops, shots, total_s, brk, payoff_span, pill_from)
    return {"audio": audio, "rate": rate, "words": words, "ranges": ranges, "brk": brk, "splits": splits, "quiet": quiet,
            "total_frames": total_frames, "total_s": total_s, "shots": shots, "pops": pops, "gaps": gaps,
            "problems": problems, "payoff_span": payoff_span, "pill_from": pill_from}


def print_timeline(tl, out=print):
    out(f"Length {tl['total_s']:.2f} s, {len(tl['words'])} words")
    for n, (a, b) in enumerate(tl["ranges"], 1):
        out(f"  line {n}: " + " ".join(f"{w['text']}@{w['start']:.2f}" for w in tl["words"][a:b]))
    for s in tl["shots"]:
        src = f"{Path(s['visual']['path']).stem} {s['src']:.2f}-{s['src'] + s['frames'] / FPS:.2f} s"
        out(f"  {s['start']:6.2f}-{s['end']:6.2f}  {s['kind']:8s} {s['frames'] / FPS:4.2f} s  {src}")
    for p in tl["pops"]:
        t = source_time(tl["shots"], p["start"])
        out(f"  pop {p['start']:6.2f}-{p['end']:6.2f}  {p['kind']:6s} on {p['word']!r} (line {p['line']})"
            + (f"  recording {t:.2f}" if t is not None else ""))
    for g in tl["gaps"]:
        out(f"  gap {g['from_s']:.2f}-{g['to_s']:.2f} ({g['seconds']:.2f} s): {g['why']}")
    for p in tl["problems"]:
        out(f"  PROBLEM: {p}")


def render(script_path, out_dir, caption_style=None, log=print):
    started = time.perf_counter()
    if not ex.FFMPEG or not ex.FFPROBE:
        raise EditError("ffmpeg and ffprobe must be on PATH")
    script = load(script_path, caption_style)
    kokoro = ex.load_kokoro()
    tl = timeline(script, kokoro)
    if tl["problems"]:
        raise EditError("the script needs fixing:\n  " + "\n  ".join(tl["problems"]))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    total_frames, total_s, shots, pops, words, brk = (tl[k] for k in ("total_frames", "total_s", "shots", "pops",
                                                                       "words", "brk"))
    warnings = [f"background shot {s['start']:.1f}-{s['end']:.1f} s lasts {s['frames'] / FPS:.2f} s, outside "
                f"{SHOT_MIN_S:g}-{SHOT_MAX_S:g}" for s in shots
                if s["kind"] == "footage" and not SHOT_MIN_S - 0.05 <= s["frames"] / FPS <= SHOT_MAX_S + 0.05]
    timing = {}
    with tempfile.TemporaryDirectory(prefix="work_", dir=out_dir) as work_name:
        work = Path(work_name)
        ex.save_wav(work / "raw.wav", tl["audio"], tl["rate"])
        voice_lufs = ex.normalise_voice(work / "raw.wav", out_dir / "voice.wav")

        # The overlay layer.
        hidden = [brk] if brk else []
        events = caption_events(script["caption_style"], words, total_s, hidden)
        zooms, pips = {}, []
        for p in pops:
            if p["kind"] == "circle":
                points, fixed, lost, path = circle_points(p, shots)
                shot = shot_at(shots, p["start"])
                p["followed"] = {"frames": len(path), "lowest_match": min((m for *_, m in path), default=None),
                                 "lost_at_s": lost}
                if not (60 <= fixed[0] <= W - 60 and ex.SAFE_TOP <= fixed[1] <= ex.SAFE_BOTTOM):
                    raise EditError(f"the circle on {p['word']!r} is off the 9:16 picture ({fixed[0]:.0f}, "
                                    f"{fixed[1]:.0f}); use a pip for it")
                ring_end = p["end"] if lost is None else min(p["end"], lost)
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
                    label = {"text": str(p["label"]).upper(), "style": "info", "x": CENTRE_X,
                             "y": PIP_Y - PIP_SIZE[1] / 2, "font": 56}
                    ex.measure_labels([label])
                    events += ex.label_events(label, p["start"] + PIP_IN_S, p["end"] - PIP_OUT_S, layers=(6, 7))
            else:
                events += pill_events(p, p["kind"])
        if brk:
            events += break_events(brk, script["break"]["text"])
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
        music, music_note = ex.pick_music(script["music"], ex.ROOT, total_s, script["title"]) if script["music"] else (None, None)
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
            render_shot(shot, work / names[-1], piece_args, work, zooms.get(id(shot)))
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
    starts = [p["start"] for p in pops]
    gaps_all = [b - a for a, b in zip(starts, starts[1:])]
    timing["total"] = round(time.perf_counter() - started, 1)
    data = {"script": str(script_path), "title": script["title"], "caption_style": script["caption_style"],
            "length_s": round(total_s, 3), "words": len(words), "encoder": encoder,
            "longest_voice_pause_s": round(tl["quiet"], 2),
            "background": {"shots": [{"kind": s["kind"], "start_s": s["start"], "end_s": s["end"],
                                      "seconds": round(s["frames"] / FPS, 3), "recording": Path(s["visual"]["path"]).stem,
                                      "from_s": s["src"], "to_s": round(s["src"] + s["frames"] / FPS, 3),
                                      "zoom": bool(zooms.get(id(s)))} for s in shots],
                           "average_s": round(sum(footage) / len(footage), 2) if footage else None},
            "pops": [{k: p[k] for k in ("kind", "start", "end", "word", "line")}
                     | {k: p[k] for k in ("hud", "text", "label", "followed", "skipped") if p.get(k)} for p in pops],
            "pop_interval_s": round(sum(gaps_all) / len(gaps_all), 2) if gaps_all else None,
            "gaps_over_2s": tl["gaps"], "break": {"from_s": brk[0], "to_s": brk[1], "text": script["break"]["text"]}
            if brk else None, "payoff_hold": tl["payoff_span"], "sfx": sfx,
            "loudness": {"voice_lufs": voice_lufs, "mix_lufs": sound.get("mix_lufs"), "final_lufs": final_lufs,
                         "final_true_peak_db": final_peak, "true_peak_guard": peak_guard},
            "music": {"file": str(music) if music else None, "chosen": music_note},
            "game_audio": sound.get("game_pieces"),
            "word_times": [{"text": w["text"], "start_s": round(w["start"], 3), "end_s": round(w["end"], 3)} for w in words],
            "warnings": warnings, "render_s": timing}
    (out_dir / "log.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Rendered {out_dir / 'short.mp4'}: {total_s:.1f} s, {script['caption_style']} captions")
    log(f"  Background: {len(footage)} shots, average {data['background']['average_s']} s; pops: {len(pops)}, "
        f"average interval {data['pop_interval_s']} s")
    for g in tl["gaps"]:
        log(f"  Gap {g['from_s']:.1f}-{g['to_s']:.1f} s ({g['seconds']:.1f} s): {g['why']}")
    log(f"  Loudness {final_lufs:.1f} LUFS, true peak {final_peak:.1f} dB; made in {timing['total']:.0f} s")
    for w in warnings:
        log(f"  WARNING: {w}")
    return data


def main():
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Edit engine v2: slow background cuts under a pop-in layer.")
    ap.add_argument("script", type=Path)
    ap.add_argument("--out", type=Path, help="the output folder")
    ap.add_argument("--caption-style", choices=CAPTION_STYLES)
    ap.add_argument("--plan", action="store_true", help="make the voice and print the timeline; render nothing")
    args = ap.parse_args()
    try:
        if args.plan:
            script = load(args.script, args.caption_style)
            print_timeline(timeline(script, ex.load_kokoro()))
        else:
            if not args.out:
                ap.error("--out FOLDER is needed to render")
            render(args.script, args.out, args.caption_style)
    except (EditError, ex.ScriptError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
