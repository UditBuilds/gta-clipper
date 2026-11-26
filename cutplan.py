"""Engine v4 (editor-v3, 2026-11-20): payoff-first editing. Plans a Short's picture before its voice.

The owner's rules after the part3 Shorts (none postable; Short 3's crash was skipped):
1. Payoff first. Every Short names its one payoff: {"from": s, "to": s, "what": "..."} in the recording. It plays whole,
   at real speed, inside one shot: never cut, never skipped. No clear payoff = no Short (the interest gate).
2. No padding: no second of the recording is shown twice. Not enough unique footage makes a shorter Short (15 s
   minimum), never a repeat. So there is no loop ending either: it replayed the first shot. The one exception (PR 29):
   an action Short's "replay" of its impact, once, at half speed, right after the payoff (replay_of).
3. Shots are spans of the recording ({"from", "to"}), not points whose length the voice decides. The voice is placed
   into the picture: each line starts at its own second ("at") and must end before its shot's cut.
4. Cuts land on sentence ends or on footage events, never on a timer.
5. Short punchy lines: at most 9 words a sentence, talking to the viewer.

Why Short 3 (part3, "bridge jam") lost its crash: engine v3 took each beat as one second of the recording ("at") and
let the voice decide how long it stayed up (a line's time split evenly over its beats). Line 2 had beats at 267.0 and
270.0 s, each up 1.53 s, so the screen showed 267.0-268.5 and then jumped to 270.0: the impact (268.7-270.1) fell in
the gap. Nothing checked that the payoff's seconds were on screen. The clipper's peak (270.0) was the game's camera
switch, not the hit (the loudest sound is at 269.0-269.5), and the evidence frames were 6 s apart, so none showed it.
Line 1's beats (265.0 for 2.4 s, 266.3 for 2.43 s) overlapped each other and line 2's first beat: the same seconds
three times. The loop ending then replayed the first beat back and forth.

Standard library only: the measuring (shake, first frame, loudness, camera cuts) is in steady.py.
"""
import re

FPS = 30
SPEED = 1.2                               # am_puck: the owner's pick by ear (Nov 23) of 1.20-1.35; the only default
                                          # (assemble.build reads it); "speed" in shorts.json changes one Short
LENGTH_S = {"action": (15.0, 25.0), "narrated": (15.0, 39.0)}
MIN_SHOT_S = 0.8                          # a shot shorter than this is a flash, not a picture
PAYOFF_S = (0.6, 8.0)                     # how long a payoff may be
SETUP_S = 1.5                             # the payoff starts at least this far in: the viewer sees the setup first
PAYOFF_LATEST = 0.85                      # ...and before this share of the Short
NEAR_S = 60.0                             # shots and the payoff stay this near the moment's window
LINE_GAP_S = 0.12                         # between two lines, at least this much quiet (explainer's GAP_S)
CUT_ROOM_S = 0.05                         # a line ends at least this long before its shot's cut
SENTENCE_CUT_S = 1.6                      # a cut is "on a sentence end" when a line ended at most this long before it
                                          # (Phase 1's natural cut as he gets in the car came 1.5 s after the line)
EVENT_SLACK_S = 0.4                       # ...or "on an event" when a shot edge is this near a footage event
LOUDEST_SLACK_S = 0.3                     # an action payoff contains the window's loudest moment, give or take this
MAX_WORDS = 9                             # in one sentence: short punchy lines (the owner's 5-9 words)
TALK_WORDS = ("you", "your", "you're", "youre", "yours", "watch", "look", "wait", "check")
# PR 29, the one exception to rule 2 (the owner, Nov 20: his hand-made crash Short worked better partly for its slow-mo
# of the hit): an action Short may replay its impact ONCE, right after the payoff, at half speed, at most 2 s on
# screen, and only when the impact itself lasts under about 1.5 s.
REPLAY_SPEED = 0.5
REPLAY_MAX_S = 2.0                        # on screen; so at most REPLAY_MAX_S * REPLAY_SPEED = 1 s of the recording
REPLAY_IMPACT_MAX_S = 1.5                 # a longer impact is not replayed
# PR 30, the first seconds (YouTube Studio, Nov 22: the hits kept 71 % and 74 % of viewers, the flops 41 % and 48 %;
# the swipe is decided in the first 2-3 s): the voice starts at once, the hook is said by 3.5 s, the next line comes
# by 4 s, and the Short ends as the talking ends (no subscribe pill), unless it ends on the slow-mo replay.
FIRST_WORD_S = 0.2                        # line 1 starts (its first word is spoken) by this
HOOK_END_S = 3.5                          # line 1, the spoken hook, has ended by this
SECOND_LINE_S = 4.0                       # line 2 starts by this
END_AFTER_WORDS_S = 0.5                   # the Short ends at most this long after its last word
CARD_MAX_WORDS = 5                        # the open card (the hit's had one): short, so it reads in a glance
# Result words: a hook, title or open card that tells the result loses the viewer ("This GTA 6 motorbike ride ends
# BADLY" lost 59 %; "Watch the black SUV in GTA 6" named what to watch and kept 71 %). A question may name one.
SPOILERS = (r"\bend(s|ed)?\s+(badly|terribly|horribly|in\s+(a\s+)?(disaster|tears|flames))", r"\bcrash(es|ed)\b",
            r"\bdie[sd]\b", r"\bexplode[sd]\b", r"\b(blows?|blew)\s+up\b", r"\b(get|gets|got|getting)\s+busted\b",
            r"\bbusted\b", r"\bwipe[sd]\s+out\b", r"\bwiped\s+out\b", r"\bfail(s|ed)\b", r"\bsurvive[sd]\b",
            r"\b(doesn'?t|does\s+not|didn'?t|did\s+not|won'?t|never)\s+make\s+it\b", r"\b(gets?|got)\s+(killed|wrecked|"
            r"caught|away)\b", r"\bkill(s|ed)\b", r"\bwreck(s|ed)\b", r"\bescape[sd]\b", r"\b(goes|went)\s+wrong\b")
# What a hook may name as the thing to watch, and the words the evidence uses for it (the vision model writes
# "pickup truck", "motorcycle", "police"). The first one the hook names must be on screen at frame 0.
SUBJECTS = {"suv": ("suv", "suvs"), "car": ("car", "cars", "sedan", "sports car", "coupe"),
            "truck": ("truck", "trucks", "pickup"), "bike": ("bike", "bikes", "motorbike", "motorcycle", "dirt bike"),
            "bus": ("bus",), "boat": ("boat", "boats", "jet ski", "jetski", "speedboat"),
            "helicopter": ("helicopter", "chopper", "heli"), "plane": ("plane", "jet", "airplane"),
            "police": ("cop", "cops", "police", "officer", "officers", "cruiser", "patrol car"),
            "bridge": ("bridge", "overpass"), "highway": ("highway", "freeway", "interstate"),
            "beach": ("beach",), "train": ("train",), "gator": ("gator", "alligator", "crocodile"),
            "animal": ("animal", "animals", "wildlife", "boar", "deer", "dog", "horse", "cow")}   # Nov 26 (rule 3)
FRAME0_S = 0.05                           # "frame 0": the evidence covers shot 1's start within this


NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def words_of(text):
    return re.findall(r"[A-Za-z0-9'’]+", str(text or ""))


def forms(word):
    """The forms of a word that count as the same word, lower case: steal, steals, stealing, stealed; stop, stops,
    stopped, stopping; survive, survives, surviving, survived; take, takes, taking, taken."""
    w = str(word or "").lower().replace("’", "'").strip(".,!?;:\"'()")
    if not w:
        return set()
    stem = w[:-1] if w.endswith("e") else w
    out = {w, w + "s", w + "es", w + "ed", w + "d", w + "ing", stem + "ing", w + "n", stem + "en"}
    if len(w) >= 3 and w[-1] not in "aeiouwxy" and w[-2] in "aeiou" and w[-3] not in "aeiou":
        out |= {w + w[-1] + "ed", w + w[-1] + "ing"}
    return out


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(text or "").strip()) if s.strip()]


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def payoff_of(short, window):
    """(payoff, problems). The Short's one payoff: {"from", "to", "what"} in the recording, within NEAR_S of the
    moment's window, PAYOFF_S long, "what" saying in 3+ words what happens. Missing or unclear: the Short is
    rejected, with the reason (the interest gate)."""
    p = short.get("payoff")
    need = ('payoff: name the one moment this Short is for, {"from": s, "to": s, "what": "what happens"} in the '
            "recording. No clear payoff, no Short")
    if not isinstance(p, dict):
        return None, [need]
    a, b, what = p.get("from"), p.get("to"), str(p.get("what") or "").strip()
    if not (number(a) and number(b)):
        return None, [need]
    problems = []
    if not PAYOFF_S[0] <= b - a <= PAYOFF_S[1]:
        problems.append(f"payoff: {a:.1f}-{b:.1f} s lasts {b - a:.1f} s; a payoff is {PAYOFF_S[0]:g}-{PAYOFF_S[1]:g} s "
                        "(the whole hit, the whole change, nothing else)")
    if len(words_of(what)) < 3:
        problems.append("payoff: \"what\" says in 3+ words what happens (it goes in the report)")
    w0, w1 = window
    if not (w0 - NEAR_S <= a and b <= w1 + NEAR_S):
        problems.append(f"payoff: {a:.1f}-{b:.1f} s is far from this moment's window ({w0:.0f}-{w1:.0f} s)")
    return {"from": float(a), "to": float(b), "what": what}, problems


def loudest_problems(payoff, loudest_s):
    """An action payoff contains the loudest moment of its window (the hit), give or take LOUDEST_SLACK_S. Short 3's
    beats showed 267.0-268.5 and 270.0-271.5 while the loudest sound, the hit, was at 269.0-269.5."""
    if payoff is None or loudest_s is None:
        return []
    if payoff["from"] - LOUDEST_SLACK_S <= loudest_s <= payoff["to"] + LOUDEST_SLACK_S:
        return []
    return [f"payoff: {payoff['from']:.1f}-{payoff['to']:.1f} s misses the loudest moment of this footage at "
            f"{loudest_s:.1f} s (the hit itself?); move the payoff onto it"]


def shots_of(short, window, payoff):
    """(shots, problems). shots: [{"from", "to", "start", "end", "focus", "inset"}] on the Short's timeline, in
    order. Each is a span of the recording, at least MIN_SHOT_S, near the window; no two share a second; the payoff
    lies inside exactly one of them, whole."""
    raw = short.get("shots")
    if not isinstance(raw, list) or not raw:
        return [], ['shots: the Short\'s pictures, in order: [{"from": s, "to": s}, ...] (spans of the recording)']
    problems, shots, t = [], [], 0.0
    w0, w1 = window
    for k, s in enumerate(raw, 1):
        where = f"shot {k}"
        if not isinstance(s, dict) or not (number(s.get("from")) and number(s.get("to"))):
            problems.append(f'{where}: {{"from": s, "to": s}}, seconds of the recording')
            continue
        a, b = float(s["from"]), float(s["to"])
        if b - a < MIN_SHOT_S:
            problems.append(f"{where}: {a:.2f}-{b:.2f} s is {b - a:.2f} s; a shot is at least {MIN_SHOT_S:g} s")
            continue
        if not (w0 - NEAR_S <= a and b <= w1 + NEAR_S):
            problems.append(f"{where}: {a:.1f}-{b:.1f} s is far from this moment's window ({w0:.0f}-{w1:.0f} s)")
        focus = s.get("focus", 0.5)
        if not number(focus) or not 0 <= focus <= 1:
            problems.append(f"{where}: \"focus\" is 0-1, where across the picture the 9:16 window sits")
            focus = 0.5
        if shots and a < shots[-1]["to"] - 1e-6:
            problems.append(f"{where} starts at {a:.2f} s, before shot {k - 1} ends ({shots[-1]['to']:.2f} s): shots go "
                            "forward through the recording and never show a second twice")
        frames = round((b - a) * FPS)
        shots.append({"n": k, "from": a, "to": b, "start": round(t, 4), "end": round(t + frames / FPS, 4),
                      "frames": frames, "focus": float(focus), "inset": s.get("inset")})
        t += frames / FPS
    if payoff is not None and shots and not problems:
        holder = [s for s in shots if s["from"] - 1e-6 <= payoff["from"] and payoff["to"] <= s["to"] + 1e-6]
        if not holder:
            problems.append(f"the payoff ({payoff['from']:.1f}-{payoff['to']:.1f} s) must play whole inside one shot: "
                            "no shot holds all of it, so part of it would be cut")
    return shots, problems


IMPACT_EVENTS = ("crash", "stunt", "jump", "fall")    # Nov 26 (rule 5): a Short of one may replay it, even narrated


def is_impact(moment):
    """Whether a moment's evidence has a crash, a stunt, a jump or a fall (an event its frames confirmed)."""
    return any(e.get("kind") == "event" and str(e.get("value", "")).lower() in IMPACT_EVENTS
               for e in moment.get("evidence") or [])


def replay_of(short, payoff, kind, loudest_s=None, impact=False):
    """(replay, problems). The Short's slow-mo replay of its impact, or None: "replay": {"from": s, "to": s} names the
    impact itself in the recording. Only in an action Short or (Nov 26) a crash or stunt Short typed narrated
    (impact), only one, inside the payoff, at most
    REPLAY_IMPACT_MAX_S long (a longer impact is not replayed), holding the loudest moment (the hit) give or take
    LOUDEST_SLACK_S. It plays at REPLAY_SPEED for at most REPLAY_MAX_S: an impact over 1 s of the recording shows the
    1 s round the hit."""
    r = short.get("replay")
    if r is None or r is False:
        return None, []
    if kind != "action" and not impact:
        return None, ["replay: only an action Short or a crash or stunt Short replays its impact (this one's evidence has "
                      "no crash, stunt, jump or fall, so it never shows a second twice)"]
    if not isinstance(r, dict) or not (number(r.get("from")) and number(r.get("to"))) or r["to"] <= r["from"]:
        return None, ['replay: {"from": s, "to": s}, the impact itself (one, inside the payoff) in the recording']
    if payoff is None:
        return None, []
    a, b = float(r["from"]), float(r["to"])
    problems = []
    if not (payoff["from"] - 1e-6 <= a and b <= payoff["to"] + 1e-6):
        problems.append(f"replay: {a:.2f}-{b:.2f} s must lie inside the payoff ({payoff['from']:.1f}-"
                        f"{payoff['to']:.1f} s): it replays the impact itself")
    if b - a > REPLAY_IMPACT_MAX_S + 1e-6:
        problems.append(f"replay: the impact lasts {b - a:.1f} s; only an impact under {REPLAY_IMPACT_MAX_S:g} s is "
                        "replayed (a long one plays once, at real speed)")
    if loudest_s is not None and not (a - LOUDEST_SLACK_S <= loudest_s <= b + LOUDEST_SLACK_S):
        problems.append(f"replay: {a:.2f}-{b:.2f} s misses the loudest moment at {loudest_s:.2f} s (the hit itself?)")
    if problems:
        return None, problems
    keep = REPLAY_MAX_S * REPLAY_SPEED
    if b - a > keep + 1e-6:
        centre = loudest_s if loudest_s is not None and a <= loudest_s <= b else (a + b) / 2
        a = min(max(a, centre - keep / 2), b - keep)
        b = a + keep
    return {"from": round(a, 4), "to": round(b, 4), "speed": REPLAY_SPEED}, []


def retime(shots):
    """Number the shots and put them end to end on the Short's timeline (a slowed shot lasts longer than its span)."""
    t = 0.0
    for k, s in enumerate(shots, 1):
        frames = round((s["to"] - s["from"]) / s.get("speed", 1.0) * FPS)
        s.update(n=k, start=round(t, 4), end=round(t + frames / FPS, 4), frames=frames)
        t += frames / FPS
    return shots


def with_replay(shots, payoff, replay):
    """(shots, notes): the replay put in right after the payoff. The payoff's shot is split at the payoff's end; the
    replay follows; then the rest of that shot, if it lasts MIN_SHOT_S or more (else it is left out, and said)."""
    i = next(k for k, s in enumerate(shots) if s["from"] - 1e-6 <= payoff["from"] and payoff["to"] <= s["to"] + 1e-6)
    h, cut, notes = shots[i], payoff["to"], []
    first = dict(h, to=cut)
    rest = dict(h, **{"from": cut, "inset": None})
    play = {"from": replay["from"], "to": replay["to"], "speed": replay["speed"], "replay": True, "focus": h["focus"],
            "inset": None}
    keep = [first, play] + ([rest] if h["to"] - cut >= MIN_SHOT_S - 1e-6 else [])
    if h["to"] - cut > 1e-6 and len(keep) == 2:
        notes.append(f"the {h['to'] - cut:.2f} s of shot {h['n']} after the payoff are left out: the replay plays there "
                     f"and a shot is at least {MIN_SHOT_S:g} s")
    return retime([dict(s) for s in shots[:i]] + keep + [dict(s) for s in shots[i + 1:]]), notes


def overlaps(shots):
    """[(k, j, from, to)] where shot k and a later shot j show the same seconds (unique_problems words it)."""
    out = []
    for i, a in enumerate(shots):
        for b in shots[i + 1:]:
            lo, hi = max(a["from"], b["from"]), min(a["to"], b["to"])
            if hi > lo + 1e-6:
                out.append((a["n"], b["n"], lo, hi))
    return out


def unique_problems(shots):
    return [f"shots {k} and {j} both show {lo:.2f}-{hi:.2f} s: no second of the recording is used twice (make the "
            "Short shorter instead)" for k, j, lo, hi in overlaps(shots)]


def to_short(src, shots):
    """The Short's second where the recording's second src shows (its first showing: never the replay), or None."""
    for s in shots:
        if not s.get("replay") and s["from"] - 1e-6 <= src <= s["to"] + 1e-6:
            return s["start"] + (src - s["from"])
    return None


def to_source(t, shots):
    """(shot, recording second) on screen at the Short's second t, or (None, None)."""
    for s in shots:
        if s["start"] - 1e-6 <= t < s["end"] + 1e-6:
            return s, s["from"] + (t - s["start"]) * s.get("speed", 1.0)
    return None, None


def replay_shot(shots):
    return next((s for s in shots if s.get("replay")), None)


def length_problems(shots, kind):
    total = shots[-1]["end"] if shots else 0.0
    lo, hi = LENGTH_S.get(kind, LENGTH_S["narrated"])
    if lo <= total <= hi:
        return []
    if total < lo:
        return [f"the shots come to {total:.1f} s; a Short is at least {lo:g} s. Add unique footage (more setup, the "
                "result): never repeat a second"]
    return [f"the shots come to {total:.1f} s; a{'n action' if kind == 'action' else ' narrated'} Short is at most "
            f"{hi:g} s: cut the setup or the ending, never the payoff"]


def payoff_place_problems(payoff, shots):
    """The setup comes first (SETUP_S at least), and the payoff before PAYOFF_LATEST of the Short."""
    if payoff is None or not shots:
        return []
    at, total = to_short(payoff["from"], shots), shots[-1]["end"]
    if at is None:
        return []
    out = []
    if at < SETUP_S:
        out.append(f"the payoff starts {at:.1f} s in; show at least {SETUP_S:g} s of setup before it")
    if at > PAYOFF_LATEST * total:
        out.append(f"the payoff starts {at / total:.0%} into the Short; it belongs before {PAYOFF_LATEST:.0%}")
    return out


def place_lines(lines, durations, shots, payoff, kind):
    """(placed, problems). Each line starts at its own recording second ("at", inside a shot), or right after the line
    before if that one runs on, and must end CUT_ROOM_S before its shot's cut: so every cut lands after a sentence
    end. In an action Short no line talks over the payoff: the game's own sound plays it."""
    placed, problems, prev_end = [], [], 0.0
    pay = (to_short(payoff["from"], shots), to_short(payoff["to"], shots)) if payoff else (None, None)
    play = replay_shot(shots)
    for n, (line, dur) in enumerate(zip(lines, durations), 1):
        at = line.get("at")
        where = f"line {n}"
        if not number(at):
            problems.append(f"{where}: \"at\" is the second of the recording where it starts (inside a shot)")
            continue
        start0 = to_short(float(at), shots)
        shot, _ = to_source(start0, shots) if start0 is not None else (None, None)
        if shot is None:
            problems.append(f"{where}: at {at:.1f} s is in none of the shots")
            continue
        start = max(start0, prev_end + (LINE_GAP_S if placed else 0.0))
        end = start + dur
        cut = shot["end"]
        if end > cut - CUT_ROOM_S + 1e-6:
            problems.append(f"{where} ({dur:.1f} s) runs {end - (cut - CUT_ROOM_S):.1f} s past the cut at {cut:.1f} s "
                            f"(the end of shot {shot['n']}): start it earlier, cut words, or show more of that shot")
        if kind == "action" and pay[0] is not None and start < pay[1] and end > pay[0]:
            problems.append(f"{where} ({start:.1f}-{end:.1f} s) talks over the payoff ({pay[0]:.1f}-{pay[1]:.1f} s): "
                            "let the game's own sound play it; say it before or after")
        if play and start < play["end"] - 1e-6 and end > play["start"] + 1e-6:
            problems.append(f"{where} ({start:.1f}-{end:.1f} s) talks over the slow-mo replay ({play['start']:.1f}-"
                            f"{play['end']:.1f} s): it plays on the game's own sound; say it before or after")
        src0 = shot["from"] + (start - shot["start"])
        placed.append({"line": n, "start": round(start, 3), "end": round(end, 3), "shot": shot["n"],
                       "src": (round(src0, 3), round(src0 + (min(end, cut) - start), 3))})
        prev_end = end
    return placed, problems


def cut_problems(shots, placed, events=()):
    """Every cut lands on a sentence end (a line ended at most SENTENCE_CUT_S before it) or on a footage event (a shot
    edge within EVENT_SLACK_S of an event: evidence, a camera cut, the payoff's edges), never on a timer."""
    out = []
    for a, b in zip(shots, shots[1:]):
        cut = a["end"]
        ends = [p["end"] for p in placed if p["end"] <= cut + 1e-6]
        on_sentence = bool(ends) and cut - max(ends) <= SENTENCE_CUT_S
        on_event = any(abs(e - a["to"]) <= EVENT_SLACK_S or abs(e - b["from"]) <= EVENT_SLACK_S for e in events)
        if a.get("replay") or b.get("replay"):     # the replay's own edges: it is the event
            continue
        if not (on_sentence or on_event):
            out.append(f"the cut at {cut:.1f} s (shot {a['n']} to {b['n']}) lands on neither a sentence end nor an "
                       f"event: end shot {a['n']} where something happens or right after a line")
    return out


def style_problems(lines):
    """(problems, flags): at most MAX_WORDS words a sentence; a flag when no line talks to the viewer."""
    out = []
    for n, line in enumerate(lines, 1):
        for s in sentences(line.get("say", "")):
            k = len(words_of(s))
            if k > MAX_WORDS:
                out.append(f"line {n}: \"{s}\" has {k} words; punchy lines are 5-{MAX_WORDS} words a sentence. Split "
                           "it or cut words")
    flags = []
    if lines and not any(w.lower().replace("’", "'") in TALK_WORDS for l in lines for w in words_of(l.get("say"))):
        flags.append("STYLE: no line talks to the viewer (\"you\", \"watch\", \"wait for it\"); read the script once")
    return out, flags


def hook_problems(short, shots, hud_words):
    """A hook that names the HUD (stars, icons, wanted) needs that proof on screen from frame 0: shot 1 starts with
    the stars' inset. Short 2 of part3 said six stars over a leaf."""
    hooks, star = short.get("hooks") or [], short.get("star")
    if not (isinstance(star, int) and 0 <= star < len(hooks)) or not shots:
        return []
    named = [w for w in words_of(hooks[star]) if w.lower() in hud_words]
    if not named:
        return []
    ins = shots[0].get("inset")
    if isinstance(ins, dict) and number(ins.get("start", 0.0)) and float(ins.get("start", 0.0)) <= 0.05:
        return []
    return [f"the hook names {named[0]!r}: show it from frame 0 (shot 1 starts with an inset, \"start\": 0)"]


def first_seconds_problems(placed):
    """The first seconds (PR 30): line 1 starts by FIRST_WORD_S and ends by HOOK_END_S; line 2 starts by
    SECOND_LINE_S. placed: place_lines' lines (their starts are their first words: the voice's lead is trimmed)."""
    if not placed:
        return []
    out, one = [], placed[0]
    if one["line"] != 1:
        return []
    if one["start"] > FIRST_WORD_S + 1e-6:
        out.append(f"line 1 starts {one['start']:.2f} s in; the first word is spoken by {FIRST_WORD_S:g} s (the swipe "
                   "is decided in the first 2-3 s): set its \"at\" to shot 1's start")
    if one["end"] > HOOK_END_S + 1e-6:
        out.append(f"line 1, the spoken hook, ends {one['end']:.1f} s in; it is said by {HOOK_END_S:g} s: cut words")
    two = next((p for p in placed if p["line"] == 2), None)
    if two and two["start"] > SECOND_LINE_S + 1e-6:
        out.append(f"line 2 starts {two['start']:.1f} s in; it starts by {SECOND_LINE_S:g} s (no long quiet after the "
                   "hook)")
    return out


def ending_problems(shots, placed):
    """The Short ends at most END_AFTER_WORDS_S after its last word (PR 30: no subscribe pill, no dead air), unless
    its last shot is the slow-mo replay (PR 29), which is the ending."""
    if not shots or not placed or shots[-1].get("replay"):
        return []
    total, last = shots[-1]["end"], max(p["end"] for p in placed)
    if total - last <= END_AFTER_WORDS_S + 1e-6:
        return []
    return [f"the Short goes on {total - last:.1f} s after the last word ({last:.1f} s of {total:.1f} s); it ends at "
            f"most {END_AFTER_WORDS_S:g} s after it: end the last shot there, or say the last line later"]


def spoilers_in(text):
    """The result words in text, outside questions: [(word, sentence)]. "Can you survive ...?" names no result."""
    out = []
    for s in sentences(str(text or "").replace("|", " ")) or []:
        if s.rstrip().endswith("?"):
            continue
        for pat in SPOILERS:
            hit = re.search(pat, s, re.IGNORECASE)
            if hit:
                out.append((hit.group(0), s))
                break
    return out


def card_words(short):
    """The open card's words: "open_card" when given, else the starred hook without a trailing "in GTA 6" (the
    title and the hashtags say GTA 6), before "?" too (Nov 26: "... in GTA 6?" kept it). None when there is no card."""
    card = short.get("open_card")
    if card:
        return " ".join(str(card).replace("|", " ").split())
    hooks, star = short.get("hooks") or [], short.get("star")
    if not (isinstance(star, int) and 0 <= star < len(hooks)):
        return None
    return re.sub(r"\s*\b(in\s+)?GTA\s*(6|VI)\s*([.!?]*)$", r"\3", str(hooks[star]).strip(), flags=re.IGNORECASE) or None


def hook_text_problems(short):
    """No hook, title or open card tells the result (questions may), and the open card has at most CARD_MAX_WORDS
    words (PR 30)."""
    out = []
    texts = [(f"hook {k}", h) for k, h in enumerate(short.get("hooks") or [], 1)]
    texts += [("title", short.get("title")), ("open card", short.get("open_card"))]
    for where, text in texts:
        for word, sentence in spoilers_in(text):
            out.append(f"{where}: \"{sentence}\" tells the result ({word!r}); name what to watch, or ask (a question "
                       "may)")
    card = card_words(short)
    if card and len(words_of(card)) > CARD_MAX_WORDS:
        out.append(f"open card: \"{card}\" has {len(words_of(card))} words; at most {CARD_MAX_WORDS}: write "
                   "\"open_card\" (what to watch, short)")
    return out


# The owner's rules of Nov 26 (two outside reviews: 80 % of winning Shorts open with a statement; the overpass jump, 20.7K,
# asked a question whose stakes showed at 0:00; the bike Short, 1.2K, opened "Watch this. Wait for it."; the airliner,
# 1.2K, had STEAL in its title and "take" in its voice):
# - no filler in a hook, the open card, the title or line 1. "Watch this" is filler only where nothing it names follows
#   ("Watch this.", "Watch this, you're ..."); "Watch this crash" names what to watch.
HOOK_FILLER = (r"\bwatch\s+this\s*(?=[.!?,;:…]|$)", r"\bwait\s+for\s+it\b", r"\bthat\s+was\s+close\b", r"\bouch\b",
               r"\blet'?s\s+see\b")
# - a hook is a statement or a command; a question only when the stakes are readable at frame 0 ("stakes_at_0": the
#   evidence that shows them at shot 1's start, and why, in a few words);
STAKES_WHY_WORDS = 3
# - the spoken hook (line 1), the open card and the title say the same key verb ("key_verb", in any of its forms).


def filler_of(text):
    """The filler a hook, card or title says ("watch this", "wait for it" ...), or None."""
    low = str(text or "").replace("’", "'")
    for pat in HOOK_FILLER:
        hit = re.search(pat, low, re.IGNORECASE)
        if hit:
            return " ".join(hit.group(0).split()).lower()
    return None


def is_question(text):
    return str(text or "").replace("|", " ").strip().endswith("?")


def stakes_problems(stakes, shots, evidence):
    """Why "stakes_at_0" doesn't show the stakes at frame 0 (empty when it does): evidence ids of the moment that cover
    shot 1's start (within FRAME0_S), and a why of STAKES_WHY_WORDS words or more."""
    if not isinstance(stakes, dict) or not isinstance(stakes.get("evidence"), list) or not stakes["evidence"]:
        return ['no "stakes_at_0": {"evidence": ["E1"], "why": "what at 0:00 shows what is at stake"}']
    out = []
    if len(words_of(stakes.get("why"))) < STAKES_WHY_WORDS:
        out.append(f"its \"why\" says in {STAKES_WHY_WORDS}+ words what at 0:00 shows the stakes")
    if not shots:
        return out
    t = shots[0]["from"]
    by_id = {e.get("id"): e for e in evidence or []}
    for i in stakes["evidence"]:
        e = by_id.get(i)
        if e is None:
            out.append(f"{i} is not this moment's evidence")
        elif not (number(e.get("from_s")) and number(e.get("to_s")) and e["from_s"] <= t + FRAME0_S and e["to_s"] >= t):
            out.append(f"{i} is not on screen at shot 1's start ({t:.2f} s)")
    return out


def key_verb_problems(short):
    """Line 1 (the spoken hook), the open card and the title each say "key_verb" in one of its forms."""
    kv = short.get("key_verb")
    if not isinstance(kv, str) or len(words_of(kv)) != 1:
        return ['key_verb: the one verb the spoken hook (line 1), the open card and the title all say ("steal", "survive", '
                '"watch"; the airliner Short\'s title said STEAL and its voice "take")']
    lines = short.get("lines") or []
    line1 = lines[0].get("say") if lines and isinstance(lines[0], dict) else None
    want, out = forms(kv), []
    for where, text in (("line 1 (the spoken hook)", line1), ("the open card", card_words(short)),
                        ("the title", short.get("title"))):
        if not any(w.lower().replace("’", "'") in want for w in words_of(text)):
            out.append(f"{where} doesn't say the key verb \"{kv}\" ({text!r}): the spoken hook, the open card and the "
                       "title share it")
    return out


def hook_rule_problems(short, shots, evidence):
    """Rules 2 and 8 of Nov 26: no filler; a question only with its stakes at frame 0; one key verb."""
    hooks, star = short.get("hooks") or [], short.get("star")
    lines = short.get("lines") or []
    line1 = lines[0].get("say") if lines and isinstance(lines[0], dict) else None
    out = []
    texts = [(f"hook {k}", h) for k, h in enumerate(hooks, 1)]
    texts += [("open card", short.get("open_card")), ("title", short.get("title")), ("line 1", line1)]
    for where, text in texts:
        f = filler_of(text)
        if f:
            out.append(f"{where}: \"{text}\" is filler (\"{f}\"): name what to watch or what is at stake")
    starred = hooks[star] if isinstance(star, int) and 0 <= star < len(hooks) else None
    asking = next(((w, t) for w, t in (("the starred hook", starred), ("line 1", line1)) if is_question(t)), None)
    if asking:
        why = stakes_problems(short.get("stakes_at_0"), shots, evidence)
        if why:
            out.append(f"{asking[0]} is a question ({asking[1]!r}): a hook is a statement or a command, and a question "
                       "only when the stakes are readable at frame 0 (" + "; ".join(why) + "). Make it a statement, or "
                       "show the stakes from 0:00")
    return out + key_verb_problems(short)


def subject_terms(name):
    return SUBJECTS.get(name, ())


def named_subjects(text):
    """The SUBJECTS a text names, in the order it names them."""
    low, found = " " + " ".join(words_of(str(text or "").lower())) + " ", []
    for name, terms in SUBJECTS.items():
        at = [low.find(f" {t} ") for t in terms if f" {t} " in low]
        if at:
            found.append((min(at), name))
    return [n for _, n in sorted(found)]


def item_names(e):
    """The SUBJECTS an evidence item supports: its own words, less what the vision check didn't see (not_seen)."""
    if e.get("kind") == "compare" or str(e.get("id", "")).startswith("C"):
        return set()
    names = set(named_subjects(e.get("value")))
    if e.get("kind") == "police":
        names.add("police")
    if "police" in (e.get("not_seen") or []):
        names.discard("police")
    return names


def frame0_problems(short, shots, evidence):
    """The main thing the hook names (the first of SUBJECTS: "the black SUV", "this bike", "this bridge", "the cops")
    is in the moment's verified evidence at shot 1's start (an item covering it within FRAME0_S). The vision model's
    sheets are 6 s apart, so this proves it is near frame 0, from the index's own words, with no new model call."""
    hooks, star = short.get("hooks") or [], short.get("star")
    if not (isinstance(star, int) and 0 <= star < len(hooks)) or not shots:
        return []
    names = named_subjects(hooks[star])
    if not names:
        return []
    t, main = shots[0]["from"], names[0]
    for e in evidence or []:
        if (number(e.get("from_s")) and number(e.get("to_s")) and e["from_s"] <= t + FRAME0_S
                and e["to_s"] >= t and main in item_names(e)):
            return []
    return [f"the hook names {main!r} ({hooks[star]!r}) but no evidence shows it at shot 1's start "
            f"({t:.2f} s): start shot 1 where it is seen, or name what is on screen there"]


def subject_of(short):
    """What is on screen at 0:00 (rule 3, Nov 26: the animal Short opened on empty grass): the first of SUBJECTS the
    starred hook names, else "subject", else what the payoff names. None when nothing names one."""
    hooks, star = short.get("hooks") or [], short.get("star")
    starred = hooks[star] if isinstance(star, int) and 0 <= star < len(hooks) else ""
    pay = short.get("payoff") if isinstance(short.get("payoff"), dict) else {}
    for text in (starred, short.get("subject"), pay.get("what")):
        names = named_subjects(text)
        if names:
            return names[0]
    return None


def subject_problems(short):
    given = short.get("subject")
    if given is not None and not named_subjects(given):
        return [f"subject: {given!r} is none of the things the first-second check knows ({', '.join(SUBJECTS)})"]
    if subject_of(short) is None:
        return ['subject: name what is on screen at 0:00 ("subject": a car, a truck, a bike, the police, an animal ...): '
                "the first second shows it, never walking, running or empty scenery"]
    return []


MAX_GAP_S = 3.5                           # rule 4 (Nov 26: the Shorts had 4-8 s with no voice)


def gap_problems(placed, shots, payoff):
    """Rule 4: between two lines at most MAX_GAP_S with no voice, the payoff's seconds and the slow-mo replay left out
    (the game's sound plays them)."""
    free = []
    if payoff:
        a, b = to_short(payoff["from"], shots), to_short(payoff["to"], shots)
        if a is not None and b is not None:
            free.append((a, b))
    play = replay_shot(shots)
    if play:
        free.append((play["start"], play["end"]))
    out = []
    for one, two in zip(placed, placed[1:]):
        pieces = [(one["end"], two["start"])]
        for x, y in free:
            pieces = [q for p0, p1 in pieces for q in ([(p0, p1)] if p1 <= x or p0 >= y else
                                                         [(p0, min(p1, x)), (max(p0, y), p1)])]
        longest = max([p1 - p0 for p0, p1 in pieces] or [0.0])
        if longest > MAX_GAP_S + 1e-6:
            out.append(f"no voice for {longest:.1f} s between line {one['line']} and line {two['line']} "
                       f"({one['end']:.1f}-{two['start']:.1f} s; the payoff and the replay don't count): at most "
                       f"{MAX_GAP_S:g} s. Add a line, or say one sooner")
    return out


HUD_KINDS = ("stars", "heat", "star_look")  # HUD readings: measured twice a second, so their edges are real moments


def events_of(moment, payoff, camera_cuts=()):
    """Footage events a cut may land on: the game's camera cuts, the HUD readings' changes, an evidence item's own
    moment (at_s), the payoff's edges. Not the edges of vision-model sheets, activity segments or event windows: those
    sit on a 6 s sampling grid, and a cut on them would be a cut on a timer. Not items of another recording (a
    compare's GTA 5 side)."""
    out = list(camera_cuts)
    for e in moment.get("evidence") or []:
        if e.get("kind") == "compare" or str(e.get("id", "")).startswith("C"):
            continue
        if number(e.get("at_s")):
            out.append(e["at_s"])
        if e.get("kind") in HUD_KINDS:
            out += [e[k] for k in ("from_s", "to_s") if number(e.get(k))]
    if payoff:
        out += [payoff["from"], payoff["to"]]
    return sorted(set(round(float(x), 3) for x in out))


def cut_reasons(shots, placed, events=()):
    """Per cut, why it passes ("sentence end", "event at s") or None: for the report."""
    out = []
    for a, b in zip(shots, shots[1:]):
        cut = a["end"]
        ends = [p["end"] for p in placed if p["end"] <= cut + 1e-6]
        if a.get("replay") or b.get("replay"):
            out.append((round(cut, 2), "the slow-mo replay" + (" starts" if b.get("replay") else " ends")))
            continue
        if ends and cut - max(ends) <= SENTENCE_CUT_S:
            out.append((round(cut, 2), f"sentence end {cut - max(ends):.2f} s before"))
            continue
        ev = [e for e in events if abs(e - a["to"]) <= EVENT_SLACK_S or abs(e - b["from"]) <= EVENT_SLACK_S]
        out.append((round(cut, 2), f"event at {ev[0]:.2f} s" if ev else None))
    return out


def line_spans(placed, count):
    """Per line, the recording seconds on screen while it is said (for truth.line_problems), or None."""
    by = {p["line"]: p["src"] for p in placed}
    return [by.get(n) for n in range(1, count + 1)]


def plan(short, moment, durations=None, loudest_s=None, camera_cuts=(), hud_words=()):
    """The whole check before anything is rendered: (plan, problems, flags). plan: {"payoff", "shots", "placed",
    "total_s", "replay"}. durations: each line's spoken seconds (without them the lines aren't placed yet). The
    written shots must never share a second; an action Short's replay (replay_of) is then put in after the payoff."""
    kind = moment.get("type")
    payoff, problems = payoff_of(short, moment["window"])
    problems += loudest_problems(payoff, loudest_s) if kind == "action" else []
    shots, more = shots_of(short, moment["window"], payoff)
    problems += more + unique_problems(shots)
    replay, more = replay_of(short, payoff, kind, loudest_s, is_impact(moment))
    problems += more
    flags = []
    if replay and shots and not problems:
        shots, notes = with_replay(shots, payoff, replay)
        flags += [f"REPLAY: {n}" for n in notes]
    if shots:
        problems += length_problems(shots, kind) + payoff_place_problems(payoff, shots)
        problems += hook_problems(short, shots, hud_words)
        problems += frame0_problems(short, shots, moment.get("evidence"))
    problems += hook_text_problems(short) + hook_rule_problems(short, shots, moment.get("evidence"))
    problems += subject_problems(short)
    lines = short.get("lines") or []
    more, style_flags = style_problems(lines)
    problems += more
    flags += style_flags
    placed = []
    if durations is not None and shots and not [p for p in problems
                                                 if p.startswith(("shot", "shots:")) or "must play whole" in p]:
        placed, more = place_lines(lines, durations, shots, payoff, kind)
        problems += more
        problems += cut_problems(shots, placed, events_of(moment, payoff, camera_cuts))
        if len(placed) == len(lines):
            problems += first_seconds_problems(placed) + ending_problems(shots, placed)
            problems += gap_problems(placed, shots, payoff)
    play = replay_shot(shots)
    return ({"payoff": payoff, "shots": shots, "placed": placed, "total_s": shots[-1]["end"] if shots else 0.0,
             "replay": dict(replay, start=play["start"], end=play["end"]) if play else None},
            list(dict.fromkeys(problems)), flags)
