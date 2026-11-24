"""The content bank (PR 32): every usable moment of every GTA 6 recording, in one place, so footage stops being wasted.

  .venv\\Scripts\\python.exe bank.py build                 index what is new, mine everything, label, write the bank
  .venv\\Scripts\\python.exe bank.py posted ID --name "what it was" [--words overpass jump] [--on 2026-11-24] [--url URL]
  .venv\\Scripts\\python.exe bank.py rejected ID [--why "dull"]
  .venv\\Scripts\\python.exe bank.py page                  write bank.html again (statuses read fresh)
  .venv\\Scripts\\python.exe bank.py migrate               once: give every entry of _used_footage.json a status

ID is the moment's id on the page (1119-135610-0469: the day, the recording's time, the second it starts). posted also
takes a finished Short's folder. Nothing here uploads, moves or deletes anything.

What it writes, in C:\\AI\\gta\\bank:
  content_bank.json   every moment: id, recording, start and end, shelf, label, how sure the label is (or "unsure"),
                      payoff, score, status
  bank.html           the review page: a shelf each, best first, 3 pictures a moment
  raw\\<recording>.json   what the mining reads, kept so it is never measured again: loudness and picture change per half
                      second (clip.py's), and the GTA 6 HUD twice a second (stars, their look, heat icons)
The vision model's answers are already kept: one per sheet in the library's index.json, the raw replies in the library's
_cache. So mining again never asks the model about a frame it has seen.

How a moment is made (code, no model):
  1. Everything the saved data can show is an event with its own seconds (events_of): a crash, explosion, jump or fall;
     the wanted stars going up or all going; the heat icons or the stars' look changing; a chase, a shootout, a loud
     peak; people reacting or fighting; an animal or a find; a mechanic the HUD shows (a warning, a prompt, "crime
     reported"); the first boat, flight, swim, chase or beach of the recordings. Nothing is invented: a kind with no
     signal in the data (a robbery that succeeds, a character switch) has no event.
  2. Events next to each other become one moment inside one clean stretch (no cutscene, menu, phone or loading):
     10-30 s for an action or proof moment, 4-10 s for a list item. What is left of the clean footage is cut into the
     miner's own windows (launch.score_window, up to 30 s, 10 s or more), every one of them: there is no "3 per 10
     minutes" limit. The calm ones rank last.
  3. Its PEAK is the loudest second round its main event (the picture's biggest change where no sound stands out).
  4. Its LABEL is what the frames at the peak show (label_of): the safety question's answer for the 4 seconds round the
     peak (a saved one where there is one, else one new look), checked against the index's own line there and the HUD.
     When they don't agree the label is "unsure" and an impact it names doesn't count. M24 (_135610, 469-489 s) was a
     "crash" because a car hit the parked pickup 4 s before the moment's peak, which is a jump off an overpass.
  5. SHELF: "action" (an impact, a shootout, a chase), "proof" (it answers an experiment or shot-list question with
     something on screen), "list" (one short fact).

STATUS (status_of) comes from _used_footage.json's spans: new; rendered (a Short was made of it and not posted: still
offered, ranked after new ones); posted (locked); rejected (never offered again). Only posted and rejected footage is
locked.

Standard library only.
"""
import argparse
import concurrent.futures
import html
import json
import math
import os
import re
import shutil
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

import clip
import fastff
import footage_short
import heavy
import index
import launch
import library
import safety
import vlm

BANK = Path(os.environ.get("GTA_BANK", r"C:\AI\gta\bank"))
BANK_FILE, PAGE_FILE = "content_bank.json", "bank.html"
VERSION = 1
GAME = "gta6"
STEP = launch.STEP
SHELVES = {"action": "Action clips", "proof": "\"I tested it\" proof", "list": "List items"}
STATUSES = ("new", "rendered", "posted", "rejected")

# --- Moments ---
MAIN_MIN_S, LIST_MIN_S = 10.0, 4.0       # an action or proof moment, and a list item, are at least this long
MAIN_MAX_S, LIST_MAX_S = 30.0, 10.0
LEAD_S, TAIL_S = 5.0, 5.0                # round the events of an action or proof moment
LIST_LEAD_S, LIST_TAIL_S = 0.5, 0.5
GROUP_GAP_S = 3.0                        # events this close are one moment (two sheets next to each other: 0 s)
MAIN_GAP_S = 8.0                         # ...for the big kinds, whose footage round them would overlap anyway
GROUP_MAX_S = 20.0                       # ...up to this long; a longer chase is cut into parts
LOUD_ACT = 3.0                           # clip.py's action score at a peak that stands out on its own
SOUND_STANDS_OUT = 0.5                   # the loudest half second counts as a peak when its z-score reaches this
HEARD = 2.0                              # a crash or an explosion is loud: its peak's loudness z-score is this or more (Nov
                                         # 24, looked at by hand: the 9 real crashes 2.0-3.5, 4 wrong ones 0.1-1.8)
PEAK_ROOM_S = 1.0                        # the peak is looked for in the main event's own seconds, this much wider (the
                                         # small kinds: exactly in them)
HUD_ROOM_S = 3.0                         # ...and this far from a HUD change
HUD_NEAR_S = 4.0                         # a HUD change this close to the peak is what the peak shows
AT_PEAK_S = (0.75, 2.0)                  # "at the peak": the frames from this long before it to this long after it (an
                                         # impact is heard first; the picture of it follows within a second or two)
PEAK_BEFORE_S, PEAK_AFTER_S = 1.0, 3.0   # a new look at the peak: 4 frames, a second apart
IMPACT = ("crash", "explosion", "stunt")
HUD_KINDS = ("star rise", "stars lost", "heat icons", "star look")
SMALL = ("reaction", "discovery", "mechanic", "first look")
WEIGHT = {"crash": 5.0, "explosion": 5.0, "stunt": 5.0, "fall": 5.0, "star rise": 4.0, "stars lost": 4.0,
          "heat icons": 3.5, "star look": 3.5, "shooting": 3.0, "chase": 2.0, "loud": 1.5, "first look": 1.2,
          "reaction": 1.0, "discovery": 1.0, "mechanic": 1.0, "calm": 0.0}
EXTRA_POINTS = {"stars lost": 1.0, "heat icons": 1.0, "star look": 1.0, "reaction": 0.5, "discovery": 0.5,
                "mechanic": 0.5, "first look": 0.5}
NAMES = {"crash": "crash", "explosion": "explosion", "stunt": "jump or stunt", "fall": "jump or fall (stunt)",
         "star rise": "wanted stars go up", "stars lost": "wanted stars all go", "heat icons": "the heat icons change",
         "star look": "the stars change how they look", "shooting": "shootout", "chase": "police chase",
         "loud": "loud action", "reaction": "someone reacts", "discovery": "a find or an animal",
         "mechanic": "a mechanic the HUD shows", "first look": "first look", "calm": "a calm stretch",
         "on_foot": "on foot", "driving": "driving", "boat": "on a boat", "flying": "flying", "swimming": "swimming",
         "shootout": "on foot with a gun", "other": "something else"}


def words(text):
    return re.compile(r"\b(?:" + text + r")\b")


# What a line of the model's (index.json's "notable", the safety answer's "note") must say for a kind. Read only where
# the line doesn't deny it (library.positive_text). Set on the 1,710 lines of Nov 19-20: scenery words that came up all
# the time (reef, yacht, "altitude changes slightly", people dancing at a concert) are left out on purpose.
KIND_WORDS = {
    "shooting": words(r"shoots?|shooting|fir(?:es|ing)|gunfire|aims?|aiming|shot at|shootout"),
    "fall": words(r"falls?|fell|falling|tumbles?|knocked (?:down|over|off)|jumps? off|leaps?|passes out|passed out|"
                  r"collapses?|faints?|thrown (?:off|from)"),
    "reaction": words(r"reacts?|flees?|cowers?|hands up|screams?|argues?|fights?|fighting|attacks?|attacked|shoves?|"
                      r"punch\w*|yells?|shouts?|shouting|angry|startled|takes photo|gestures|threatens?"),
    "discovery": words(r"effig(?:y|ies)|carved|statues?|strange noises|haunted|treasure|doubloons|ruins|caves?|"
                       r"skeletons?|sharks?|alligators?|gators?|snakes?|manta rays?|boars?|jellyfish|parrots?|dolphins?|"
                       r"turtles?|eels?|dogs?|mural|metal detector|hidden"),
    "mechanic": words(r"heat warning|heat cleared|wanted description|crime reported|fuel low|low fuel|security warning|"
                      r"police pursuit notification|police warning|warning about|altitude warning|hotwir\w*|lockpick\w*|"
                      r"stolen|steals?|barbell|workout|squats?|wardrobe|pay 'n' spray|outfit purchase|buying gear|"
                      r"taxi call|repairs?|wasted|busted|haircut|tattoo"),
}
# A calm stretch's label is the index's activity when the look at its peak says it too.
ACTIVITY_WORDS = {"driving": words(r"driv\w*|rides?|riding|car|truck|suv|van|motor\w*|vehicle|pickup"),
                  "on_foot": words(r"walk\w*|runs?|running|stands?|standing|man|woman|player|character|men|people|two"),
                  "boat": words(r"boat|yacht|jet ski|ship|steer\w*|navigat\w*"),
                  "flying": words(r"fl(?:y|ies|ying)|planes?|biplane|helicopter|airplane|parachut\w*"),
                  "swimming": words(r"swim\w*|div\w*|underwater|water")}
# An experiment or shot-list question counts as answered on screen only with one of these events in the moment; a
# question with no entry here asks for a look, not an answer (a drive, a walk), so nothing proves it.
NEEDS = {"wanted-system": {"star rise", "stars lost", "star look"}, "police-chase": {"chase", "star rise"},
         "combat-weapons": {"shooting"}, "exploring-map": {"discovery"}, "heist-robbery": {"star rise", "shooting"},
         "stealing-cars": {"mechanic", "star rise"}, "cops-outfit-car-change": {"stars lost", "heat icons", "star look"},
         "big-chases": {"chase"}, "f2-lose-stars-car-swap": {"stars lost", "heat icons"},
         "f2-lose-stars-outfit": {"stars lost", "heat icons"}, "f2-hide-in-water": {"stars lost", "star rise", "chase"},
         "f4-first-car": {"first look"}, "f4-first-chase": {"first look", "chase"}, "f4-first-beach": {"first look"},
         "f4-first-switch": {"first look"}, "f1-police-chase": {"chase"}, "f1-losing-the-stars": {"stars lost"},
         "f1-night-rain-drive": {"star rise", "chase", "crash", "stunt", "fall"}, "f1-shootout": {"shooting"},
         "f1-sports-car": {"crash", "stunt", "chase", "loud"}, "f3-three-things-chase": {"chase"}}
# ...and some ask for something the index's lines must say, in the moment or this many seconds before it: without it the
# stars going away answers "lose the stars", not "does a new car (or an outfit) lose them?".
MUST_SAY = {"f2-lose-stars-car-swap": (words(r"exits?|enters?|gets? (?:in|into|out)|hotwir\w*|steals?|stolen|another (?:car|truck)"), 45.0),
            "f1-night-rain-drive": (words(r"night|nighttime|rain|rainy|raining"), 0.0),     # (not "train tracks")
            "f2-lose-stars-outfit": (words(r"outfits?|cloth\w*|wardrobe|changing room|mannequins?"), 60.0),
            "stealing-cars": (words(r"steals?|stolen|hotwir\w*|lockpick\w*|carjack\w*|pulls? .* out"), 20.0),
            "heist-robbery": (words(r"stores?|shops?|bank|counter|cashier|clerk|register|vault|robs?|robbery"), 20.0)}
FIRST = ("boat", "flying", "swimming", "chase", "driving", "beach")
RENDERED_COST = 2.0      # picker: a moment a Short was already rendered from (not posted) is worth this much less


class BankError(Exception):
    """The bank can't go on; the text says why and what to do."""


def log_line(m):
    print(m, flush=True)


def read_bank(bank_dir=None):
    return index.read_json(Path(bank_dir or BANK) / BANK_FILE)


# --- The recordings and their saved data ---

def newest_clip_run(stem, shorts_dir, bank_dir):
    """The newest finished clip.py run of a recording (a folder with timeline.csv), in any /launch run or the bank."""
    found = [p.parent for base in (Path(shorts_dir).glob("launch-*/clip_runs"), [Path(bank_dir) / "clip_runs"])
             for root in base for p in (root / stem).glob("*/timeline.csv")]
    return max(found, key=lambda p: p.name, default=None)


def series_of(run, n):
    """(action score, loudness z-score, picture-change z-score) per half second of one clip.py run; zeros without one."""
    import csv
    loud, change = [0.0] * n, [0.0] * n
    if run is not None and (Path(run) / "timeline.csv").is_file():
        with open(Path(run) / "timeline.csv", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))[:n]
        loud = [float(r["audio_z"]) for r in rows] + [0.0] * (n - len(rows))
        change = [float(r["visual_z"]) for r in rows] + [0.0] * (n - len(rows))
    return launch.action_series(run, n), loud, change


def raw_of(idx, rec, shorts_dir, bank_dir, hud6=None, log=log_line):
    """The recording's raw measurements (raw\\<stem>.json), made once and then read: clip.py's two series and the GTA 6
    HUD's close reading over every stretch with stars. Made again only when clip.py or the HUD was read anew."""
    stem, n = rec["stem"], rec["n"]
    path = Path(bank_dir) / "raw" / f"{stem}.json"
    run = newest_clip_run(stem, shorts_dir, bank_dir)
    key = {"clip_run": str(run) if run else None, "profile": rec["profile"], "indexed": idx.get("indexed"), "n": n}
    saved = index.read_json(path)
    if saved and saved.get("key") == key:
        return saved
    if saved and run is None and dict(saved.get("key") or {}, clip_run=None) == key:
        return saved                       # the run folder it came from is gone: what was measured stays
    act, loud, change = series_of(run, n)
    stretches, series, events = [], [], []
    if rec["mode"] == "hud":
        try:
            prof = clip.load_profile(rec["profile"])
        except clip.ProfileError:
            prof = None
        if prof is not None and getattr(prof, "star_method", "outline") == "gta6":
            import proof
            read = hud6 or proof.hud6
            for a, b in clip.runs([c > 0 for c in rec["count"]]):
                if b - a < launch.STAR_STRETCH_S:
                    continue
                part = read(rec["path"], a, b, prof)
                stretches.append([a, b])
                series += [list(x) for x in part]
                events += proof.hud6_events(part)
    saved = {"key": key, "stem": stem, "act": [round(x, 3) for x in act], "loud": loud, "change": change,
             "hud6": {"stretches": stretches, "series": series, "events": events}}
    index.write_json(path, saved)
    log(f"  raw data of {stem[-14:]} kept: {n} half seconds, {len(series)} HUD readings, {len(events)} HUD changes")
    return saved


def load_recs(lib_dir=None, shorts_dir=None, bank_dir=None, stems=None, hud6=None, log=log_line, game=GAME):
    """{stem: what mining reads} of every recording of the game in the library whose file is still there, in the order
    they were played. stems: only these."""
    lib_dir, bank_dir = Path(lib_dir or index.LIBRARY), Path(bank_dir or BANK)
    shorts_dir = Path(shorts_dir or launch.SHORTS_DIR)
    lib = index.read_json(lib_dir / "library.json") or {}
    out, offset = {}, 0.0
    names = sorted(lib.get("videos") or {}, key=lambda s: (re.findall(r"\d{14}", s) or [s])[-1])
    for stem in names:
        if stems is not None and stem not in stems:
            continue
        idx = index.read_json(lib_dir / stem / "index.json")
        if not idx or not Path(idx["video"]["path"]).is_file() or index.off_limits(idx["video"]["path"]):
            continue
        profile = idx["profile"]["name"]
        if profile.removesuffix("_nohud") != game:
            continue
        idx["_dir"] = str(lib_dir / stem)
        mode = "nohud" if profile.endswith("_nohud") else "hud"
        n = math.ceil(idx["video"]["duration_s"] / STEP)
        rec = launch.recording_data(stem, idx, [0.0] * n, mode, offset)
        rec["raw"] = raw_of(idx, rec, shorts_dir, bank_dir, hud6, log)
        rec["act"] = list(rec["raw"]["act"])
        out[stem] = rec
        offset += idx["video"]["duration_s"]
    return out


def first_looks(recs):
    """{(stem, second): what}: the first boat, flight, swim, chase, drive (15 s or more) and beach of the recordings."""
    out, seen = {}, set()
    for rec in recs.values():
        for s in rec["idx"]["segments"]:
            a = s["activity"]
            if not s["never_use"] and a in FIRST and a not in seen and s["duration_s"] >= (15.0 if a == "driving" else 6.0):
                seen.add(a)
                out[(rec["stem"], s["start_s"])] = a
        for s in rec["sheets"]:
            if "beach" not in seen and re.search(r"\b(beach|sand)\b", library.positive_text(s.get("notable") or "")):
                seen.add("beach")
                out[(rec["stem"], s["t"])] = "beach"
    return out


# --- 1. Events ---

def kinds_in(text):
    """The kinds a line of the model's shows."""
    low = library.positive_text(text or "")
    kinds = {k for k, rx in KIND_WORDS.items() if rx.search(low)}
    # "avoids collision" is none; a wreck or wreckage is there before or after one (a diver's, burnt cars after a fight)
    hit = re.sub(r"avoid\w*\s+(?:a\s+|the\s+)?(?:collisions?|crash\w*)|wreck\w*", " ", text or "", flags=re.I)
    kinds |= launch.event_kinds(hit) & set(IMPACT)
    return kinds


def span_at(rec, t):
    """The clean stretch (guarded at both ends) the second t lies in, or None."""
    for a, b in rec["spans"]:
        if a <= t < b:
            lo, hi = a + launch.GUARD_S, b - launch.GUARD_S
            return (lo, hi, (a, b)) if hi - lo >= LIST_MIN_S else None
    return None


def parts(a, b, longest=GROUP_MAX_S):
    """a..b in equal parts of at most `longest` seconds."""
    k = max(1, math.ceil((b - a) / longest - 1e-9))
    return [(a + (b - a) * i / k, a + (b - a) * (i + 1) / k) for i in range(k)]


def events_of(rec, firsts=None):
    """Every event the saved data shows, in time order: [{"t0", "t1", "kind", "src", "text"}]. A HUD change has t0 =
    t1."""
    out = []

    def add(t0, t1, kind, src, text=""):
        out.append({"t0": round(t0, 2), "t1": round(t1, 2), "kind": kind, "src": src, "text": text})
    for s in rec["sheets"]:
        for k in sorted(kinds_in(s.get("notable")) - {"shooting"}):     # (a shootout is an index segment, below)
            add(s["t"], min(rec["duration_s"], s["t"] + rec["every"]), k, "index line", s["notable"])
    if rec["mode"] == "hud":
        for r in rec["rises"]:
            add(r["change_s"], r["change_s"], "star rise", "HUD", f"{r['stars_before']} to {r['stars_after']} stars")
        for r in rec["rises"]:
            later = [x["change_s"] for x in rec["rises"] if x["change_s"] > r["change_s"]]
            lost = launch.stars_lost(rec, r["change_s"], later[0] if later else rec["duration_s"])
            if lost:
                add(lost, lost, "stars lost", "HUD", "no stars left")
        for e in rec.get("raw", {}).get("hud6", {}).get("events", []):
            kind = {"heat": "heat icons", "star look": "star look"}[e["kind"]]
            add(e["at_s"], e["at_s"], kind, "HUD", f"{e['before']} to {e['after']}")
    for s in rec["idx"]["segments"]:
        if s["never_use"] or s["duration_s"] < 4.0:
            continue
        if s["activity"] == "chase":
            for a, b in parts(s["start_s"], s["end_s"]):
                add(a, b, "chase", "HUD" if str(s.get("decided_by", "")).startswith("hud") else "index", "")
        if s["activity"] == "shootout":
            for a, b in parts(s["start_s"], s["end_s"]):
                add(a, b, "shooting", "index", "")
    act = rec["act"]
    for i in range(1, len(act) - 1):
        if act[i] >= LOUD_ACT and act[i] >= max(act[max(0, i - 12):i + 13]):
            add(i * STEP, i * STEP, "loud", "sound and picture", f"action score {act[i]:.1f}")
    for (stem, t), what in (firsts or {}).items():
        if stem == rec["stem"]:
            add(t, min(rec["duration_s"], t + 10.0), "first look", "index", f"the first {what} of the recordings")
    return sorted(out, key=lambda e: (e["t0"], e["t1"], e["kind"]))


# --- 2. Moments ---

def groups_of(events, rec):
    """Events next to each other, in the same clean stretch, as lists: the big ones (an impact, the HUD, a chase ...)
    together, the small kinds (a reaction, a find, a mechanic) each on its own, so none hides another."""
    out, left = [], {"no clean footage": 0}
    families = {"main": [e for e in events if e["kind"] not in SMALL]}
    for k in SMALL:
        families[k] = [e for e in events if e["kind"] == k]
    for name, evs in families.items():
        group, gap = [], MAIN_GAP_S if name == "main" else GROUP_GAP_S
        for e in evs:
            span = span_at(rec, (e["t0"] + e["t1"]) / 2)
            if span is None:
                left["no clean footage"] += 1
                continue
            if group and (span[2] != group[0]["span"][2] or e["t0"] - max(x["t1"] for x in group) > gap
                          or e["t1"] - group[0]["t0"] > GROUP_MAX_S):
                out.append((name, group))
                group = []
            group.append(dict(e, span=span))
        if group:
            out.append((name, group))
    return out, left


def peak_of(rec, main):
    """(the peak second, how it was found): the loudest half second round the main event, or the picture's biggest
    change where no sound stands out, or the action score's best."""
    room = HUD_ROOM_S if main["t0"] == main["t1"] else 0.0 if main["kind"] in SMALL else PEAK_ROOM_S
    lo, hi, _ = main["span"]
    a, b = max(lo, main["t0"] - room), min(hi, main["t1"] + room)
    i0, i1 = int(a / STEP), max(int(a / STEP) + 1, min(rec["n"], int(math.ceil(b / STEP))))
    raw = rec.get("raw") or {}
    loud, change = raw.get("loud") or [0.0] * rec["n"], raw.get("change") or [0.0] * rec["n"]
    steps = range(i0, min(i1, rec["n"])) or range(max(0, min(i0, rec["n"] - 1)), rec["n"])
    best = max(steps, key=lambda i: (loud[i], rec["act"][i]))
    if loud[best] >= SOUND_STANDS_OUT:
        return round(best * STEP, 2), "loudest"
    best = max(steps, key=lambda i: (change[i], rec["act"][i]))
    if change[best] > 0:
        return round(best * STEP, 2), "most change"
    return round(main["t0"] if main["t0"] == main["t1"] else (main["t0"] + main["t1"]) / 2, 2), "the event itself"


def moment_id(stem, start):
    """1119-135610-0469: the day and time the recording started, and the moment's first second."""
    found = re.search(r"20\d{2}(\d{4})(\d{6})", stem)
    return (f"{found.group(1)}-{found.group(2)}" if found else stem[-14:]) + f"-{int(start):04d}"


def window_of(family, group, peak):
    """(start, end) of a moment round its events, inside its clean stretch; None when that has under LIST_MIN_S."""
    lo, hi, _ = group[0]["span"]
    t0, t1 = min(e["t0"] for e in group), max(e["t1"] for e in group)
    main = family == "main" or family == "first look"
    lead, tail, longest = (LEAD_S, TAIL_S, MAIN_MAX_S) if main else (LIST_LEAD_S, LIST_TAIL_S, LIST_MAX_S)
    a, b = max(lo, t0 - lead), min(hi, t1 + tail)
    if b - a > longest:
        a = min(max(a, peak - longest * 0.5), b - longest)
        b = a + longest
    need = MAIN_MIN_S if main else LIST_MIN_S
    if b - a < need:                                          # as long as the clean footage round it allows
        a, b = max(lo, min(a, b - need)), min(hi, max(b, a + need))
    return (round(a, 2), round(b, 2)) if b - a >= LIST_MIN_S - 1e-6 else None


def apart(found):
    """Two moments of the big kinds next to each other share no footage where both keep 10 s and their peak without it."""
    main = sorted((m for m in found if m["family"] == "main"), key=lambda m: m["peak_s"])
    for p, m in zip(main, main[1:]):
        if p["end_s"] <= m["start_s"] or p["peak_s"] >= m["peak_s"]:
            continue
        cut = (max(m["start_s"], p["peak_s"] + 1.0) + min(p["end_s"], m["peak_s"] - 1.0)) / 2
        if p["peak_s"] + 1.0 <= cut <= m["peak_s"] - 1.0:
            if cut - p["start_s"] >= MAIN_MIN_S:
                p["end_s"] = round(cut, 2)
            if m["end_s"] - max(cut, p["end_s"]) >= MAIN_MIN_S and m["start_s"] < p["end_s"]:
                m["start_s"] = p["end_s"]


def calm_windows(rec, taken):
    """The rest of the clean footage as the miner's own windows (launch.score_window: best first, up to 30 s, 10 s or
    more, every 2 s), apart from each other and from the moments already taken. Every one is kept."""
    free = []
    for a, b in rec["spans"]:
        lo, hi = a + launch.GUARD_S, b - launch.GUARD_S
        for x, y in sorted(taken):
            if y <= lo or x >= hi:
                continue
            if x - lo >= MAIN_MIN_S:
                free.append((lo, x, (a, b)))
            lo = max(lo, y)
        if hi - lo >= MAIN_MIN_S:
            free.append((lo, hi, (a, b)))
    out = []
    for lo, hi, span in free:
        todo = [(lo, hi)]
        while todo:
            x, y = todo.pop()
            if y - x < MAIN_MIN_S - 1e-6:
                continue
            length = min(MAIN_MAX_S, y - x)
            # a piece under 10 s that would be left over at an end goes into the window
            starts, s = [], x
            while s + length <= y + 1e-6:
                starts.append(s)
                s += launch.WINDOW_STEP_S
            a = max(starts, key=lambda s: (launch.score_window(rec, s, s + length)[0], -s))
            out.append((round(a, 2), round(a + length, 2), span))
            todo += [(x, a), (a + length, y)]
    return sorted(out)


def mine_all(rec, firsts=None):
    """(moments, what was left out) of one recording: every event in a moment, none dropped but those with under 4 s of
    clean footage, and the rest of the clean footage as calm windows. Moments of different families may overlap (a list
    item inside a chase); two of the big kinds next to each other are kept apart where they can be."""
    moments = []
    groups, left = groups_of(events_of(rec, firsts), rec)
    left["under 4 s of clean footage"] = 0

    def put(family, main, kinds, a, b, peak, how, span, events):
        moments.append({"id": None, "video": rec["stem"], "path": rec["path"], "profile": rec["profile"],
                        "mode": rec["mode"], "start_s": a, "end_s": b, "span": [round(span[0], 2), round(span[1], 2)],
                        "family": family, "main": main, "kinds": kinds, "peak_s": min(max(peak, a), b), "peak_by": how,
                        "events": [{k: e[k] for k in ("t0", "t1", "kind", "src", "text")} for e in events]})
    for family, group in groups:
        main = max(group, key=lambda e: (WEIGHT[e["kind"]], -e["t0"]))
        peak, how = peak_of(rec, main)
        win = window_of(family, group, peak)
        if win is None:
            left["under 4 s of clean footage"] += 1
            continue
        twin = next((m for m in moments if family in SMALL and m["family"] in SMALL and "first look" not in
                     (family, m["family"]) and (m["start_s"], m["end_s"]) == win), None)
        if twin is not None:                # a reaction and a find on the very same seconds: one list item, both kinds
            twin["kinds"] = sorted(set(twin["kinds"]) | {e["kind"] for e in group})
            twin["events"] += [{k: e[k] for k in ("t0", "t1", "kind", "src", "text")} for e in group]
            continue
        put(family, main["kind"], sorted({e["kind"] for e in group}), *win, peak, how, group[0]["span"][2], group)
    apart(moments)
    for a, b, span in calm_windows(rec, [(m["start_s"], m["end_s"]) for m in moments if m["family"] == "main"]):
        peak, how = peak_of(rec, {"t0": a, "t1": b, "kind": "calm", "span": (a, b, span)})
        put("calm", "calm", [], a, b, peak, how, span, [])
    for m in moments:
        points, terms = launch.score_window(rec, m["start_s"], m["end_s"])
        for k in m["kinds"]:
            if k in EXTRA_POINTS:
                terms[k.replace(" ", "_")] = EXTRA_POINTS[k]
        m.update(id=moment_id(rec["stem"], m["start_s"]), terms=terms,
                 session_s=round(rec["offset_s"] + (m["start_s"] + m["end_s"]) / 2, 1),
                 score=round(sum(v for v in terms.values() if isinstance(v, (int, float))), 3),
                 mined_as=launch.category_of(terms) if m["family"] in ("main", "calm") else m["main"])
    taken = {}
    for m in sorted(moments, key=lambda m: (m["start_s"], -WEIGHT[m["main"]])):     # two ids the same second: a, b ...
        k = taken.setdefault(m["id"], 0)
        taken[m["id"]] += 1
        if k:
            m["id"] += chr(ord("a") + k)
    return sorted(moments, key=lambda m: m["start_s"]), left


# --- 3. The label: what the frames at the peak show ---

def saved_answers(cache_dir, stem, version=None):
    """The safety question's saved answers of a recording: [{"from_s", "to_s", "every_s", "answer"}] (vlm.cache_save's
    files)."""
    out = []
    folder = Path(cache_dir) / stem
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        entry = index.read_json(f) or {}
        fields, record = entry.get("fields") or {}, entry.get("record") or {}
        if (fields.get("prompt_version") == (version or safety.PROMPT_VERSION) and "from_ms" in fields
                and "band" not in fields and record.get("answer")):
            out.append({"from_s": fields["from_ms"] / 1000, "to_s": fields["to_ms"] / 1000,
                        "every_s": fields["every_s"], "answer": record["answer"]})
    return sorted(out, key=lambda s: s["from_s"])


def frames_at(s, peak):
    """How many of a sheet's frames lie at the peak (AT_PEAK_S)."""
    n = max(1, round((s["to_s"] - s["from_s"]) / s["every_s"]))
    return sum(1 for k in range(n) if peak - AT_PEAK_S[0] <= s["from_s"] + k * s["every_s"] <= peak + AT_PEAK_S[1])


def answers_at(answers, peak):
    """The saved answers with frames at the peak, the ones with most of them first; [] when they hold fewer than 2 frames
    there between them, or none after it."""
    here = [(frames_at(s, peak), s) for s in answers if s["every_s"] <= 1.0]
    here = sorted([x for x in here if x[0]], key=lambda x: (-x[0], abs((x[1]["from_s"] + x[1]["to_s"]) / 2 - peak)))
    after = any(s["to_s"] - s["every_s"] >= peak + 0.5 for _, s in here)
    return [s for _, s in here] if sum(n for n, _ in here) >= 2 and after else []


def look_at(m, answers, checker, work):
    """(the answers for the frames at the moment's peak, how many times the model was asked). A saved answer is used
    where there is one; else the safety question is asked once about the 4 seconds round the peak."""
    here = answers_at(answers, m["peak_s"])
    if here or checker is None:
        return here, 0
    a = max(0.0, m["peak_s"] - PEAK_BEFORE_S)
    before = checker.calls
    sheets = checker.look(m["path"], a, a + PEAK_BEFORE_S + PEAK_AFTER_S, 1.0, work)
    new = [{"from_s": s["from_s"], "to_s": s["to_s"], "every_s": 1.0, "answer": s["answer"]} for s in sheets if s["answer"]]
    answers += new
    return new, checker.calls - before


SEEN_AS = {"crash": "crash", "explosion": "explosion", "stunt": "stunt", "shooting": "shooting"}


def label_of(m, rec, sheets):
    """What the frames at the peak show: {"kind", "text", "sure", "confidence", "why", "mined_as"}.

    sheets: the safety answers for the frames at the peak. The kind comes from them and from the HUD at the peak, never
    from what the moment was mined as:
      crash, explosion, a vehicle's jump, shooting: the safety question ticks it there AND a line says it (its own note,
      or the index's line at the peak): neither a word nor a tick alone is enough (a truck "parked in an alleyway" was
      ticked as a crash); several looks there: the ones with most of the frames; a crash and an explosion must also be
      HEARD there (where the sound was measured);
      the stars, the heat icons: the HUD itself changes within 4 s of the peak (read by code);
      a fall, a reaction, a find, a mechanic: the safety note says it AND the index's own line there says it (two
      separate looks, different frames); a chase: the HUD shows stars at the peak while driving.
    Where none of these holds, or the note says something the other look doesn't, the label is "unsure"."""
    peak = m["peak_s"]
    notes = [s["answer"]["note"] for s in sheets]
    # several looks at the same seconds may disagree: what the looks with most of the frames there say counts
    weight = [(max(1, frames_at(s, peak)), s["answer"]) for s in sheets]
    seen = {k for k, q in SEEN_AS.items() if sum(w for w, a in weight if a.get(q)) * 2 >= sum(w for w, _ in weight) > 0}
    said = set().union(*[kinds_in(n) for n in notes]) if notes else set()
    lines = [s["notable"] for s in rec["sheets"] if s["t"] - 1.0 <= peak < s["t"] + rec["every"] + 1.0]
    line_kinds = set().union(*[kinds_in(x) for x in lines]) if lines else set()
    hud = {e["kind"]: e for e in m["events"] if e["kind"] in HUD_KINDS and abs(e["t0"] - peak) <= HUD_NEAR_S}
    text = notes[0] if notes else (lines[0] if lines else "")
    out = {"mined_as": m["mined_as"], "text": text, "note": notes[0] if notes else None,
           "line": lines[0] if lines else None}

    def is_(kind, confidence, why):
        return dict(out, kind=kind, name=NAMES.get(kind, kind), sure=True, confidence=confidence, why=why)
    if not sheets and not hud:
        return dict(out, kind="unsure", name="unsure", sure=False, confidence=0.0,
                    why="the frames at its peak have not been looked at yet")

    def telling(k):
        """The note of a look that shows k, else the index's line that says it, else the first note."""
        ticked = [s["answer"]["note"] for s in sheets if s["answer"].get(SEEN_AS.get(k, ""))]
        return next((n for n in ticked + notes if k in kinds_in(n)),
                    next((x for x in lines if k in kinds_in(x)), text))
    ticked_only, i = [], min(rec["n"] - 1, int(peak / STEP))
    loud = (rec.get("raw") or {}).get("loud") or []
    z = max(loud[max(0, i - 1):i + 2], default=0.0)
    for k in IMPACT:
        if k in seen and (k in said or k in line_kinds) and k != "stunt" and any(loud) and z < HEARD:
            return dict(out, kind="unsure", name="unsure", sure=False, confidence=0.4,
                        why=f"the look at the peak and a line say {safety.SEEN[k]}, but nothing that loud is heard there "
                            f"(loudness {z:.1f}, a real one {HEARD:g} or more): look")
        if k in seen and (k in said or k in line_kinds):
            both = k in said and k in line_kinds
            return dict(is_(k, 0.95 if both else 0.85, f"the peak's frames show {safety.SEEN[k]}"
                            + (" and both lines say so" if both else "")), text=telling(k))
        if k in seen:
            ticked_only.append(k)
    if "fall" in said and "fall" in line_kinds:
        return dict(is_("fall", 0.8, "both looks at the peak (the safety note and the index's line) see a fall or a jump; no "
                                     "crash, explosion or vehicle in the air is seen there"), text=telling("fall"))
    if "shooting" in seen and ("shooting" in said or "shooting" in line_kinds):
        return dict(is_("shooting", 0.9, "the peak's frames show shooting"), text=telling("shooting"))
    for k in HUD_KINDS:
        if k in hud:
            got = is_(k, 1.0, f"the HUD changes at {launch.hms(hud[k]['t0'])} ({hud[k]['text']})")
            return dict(got, name=f"{NAMES[k]} ({hud[k]['text']})")
    for k in ("reaction", "discovery", "mechanic"):
        if k in m["kinds"] and k in said and k in line_kinds:
            return is_(k, 0.7, "both looks at the peak say it")
    seg = next((s for s in rec["idx"]["segments"] if s["start_s"] <= peak < s["end_s"]), None)
    if seg is not None and seg["activity"] == "chase" and rec["count"][i]:
        return is_("chase", 0.9, f"the HUD shows {rec['count'][i]} star(s) at the peak while driving")
    if m["main"] == "first look" and seg is not None:
        what = m["events"][0]["text"]
        ok = seg["activity"] in what or ("beach" in what and any("beach" in x.lower() for x in notes))
        if ok:
            return is_("first look", 0.7, what)
    if ticked_only:
        return dict(out, kind="unsure", name="unsure", sure=False, confidence=0.4,
                    why=f"the look at the peak ticks {safety.SEEN[ticked_only[0]]}, but its own note doesn't say so: \"{text}\"")
    if m["main"] == "calm" and sheets and seg is not None:
        rx = ACTIVITY_WORDS.get(seg["activity"])
        if rx is not None and any(rx.search(library.positive_text(x)) for x in notes):
            return is_(seg["activity"], 0.6, "the index's activity there, and the look at the peak says it too")
    if not sheets:
        why = "the frames at its peak have not been looked at yet"
    elif m["main"] == "calm":
        why = f"nothing clear at its peak: \"{text}\""
    elif m["main"] in ("loud",):
        why = "loud, but the frames at the peak show no clear event"
    else:
        why = (f"it was mined as {NAMES.get(m['main'], m['main'])}, but the frames at its peak don't show that: "
               f"\"{text}\"")
    return dict(out, kind="unsure", name="unsure", sure=False, confidence=0.3 if sheets else 0.0, why=why)


def impact_of(m):
    """The impact a moment's sure label names, in the picker's words (a fall counts as a stunt), or None."""
    label = m.get("label") or {}
    kind = "stunt" if label.get("kind") == "fall" else label.get("kind")
    return kind if label.get("sure") and kind in IMPACT else None


def set_label(m, rec, sheets):
    """The moment's label from its peak, its points and its payoff with it: an impact counts only where the peak's
    frames show it; a fall both looks see counts as a stunt (as the owner posted M24)."""
    label = label_of(m, rec, sheets)
    terms = dict(m["terms"])
    for k in IMPACT:
        terms.pop(k, None)
    impact = impact_of({"label": label})
    if impact:
        terms[impact] = launch.EVENTS[impact][0]
    if label["sure"] and label["kind"] in ("stars lost", "heat icons", "star look"):
        terms["hud6"] = 1.0                        # a HUD change, as the topic candidates count it
    if terms.get("activity_label") == "shootout" and not (label["sure"] and label["kind"] == "shooting"):
        terms.pop("activity", None)
        terms.pop("activity_label", None)
    if terms.get("police") and not terms.get("stars") and sheets and not any(s["answer"].get("police") for s in sheets):
        terms.pop("police")
    m["label"], m["terms"] = label, terms
    m["score"] = round(sum(v for v in terms.values() if isinstance(v, (int, float))), 3)
    m["category"] = impact or (launch.category_of(terms) if m["family"] in ("main", "calm") else m["main"])
    tier, name, why = launch.payoff_strength(dict(m, point=None))
    m["payoff"] = {"tier": tier, "label": name, "why": why}
    m["subtitles"] = any(s["answer"].get("subtitles") for s in sheets)
    return m


def blocked(m, rec, sheets):
    """Why the moment can't be used at all, from the frames at its peak and the index's lines, or []: a phone, a menu, a
    cutscene, a loading screen (in frames that lie in the moment: a saved look may reach past its end); a child near
    violence, a body with blood (safety.never_shown). The whole footage is checked again before anything is picked."""
    frames = [dict(s, file="", error=None) for s in sheets]
    inside = [s for s in frames if m["start_s"] - 1.0 <= s["from_s"] and s["to_s"] - s["every_s"] <= m["end_s"]]
    why = [what for _, _, what in safety.found(inside, safety.BLOCKING)]
    return list(dict.fromkeys(why)) + safety.never_shown(rec, frames, m["start_s"], m["end_s"])


# --- 4. Questions and shelves ---

def questions(recs, shotlist=None):
    """Every experiment and shot-list topic as {"id", "kind", "question", "rows": [(stem, from, to)]}: where its library
    query matches."""
    data = shotlist if shotlist is not None else library.load_shotlist()
    indexes = [r["idx"] for r in recs.values()]
    out = []

    def rows_of(query):
        try:
            rows = library.find(indexes, **query)
        except library.LibraryError:
            return []
        got = []
        for r in rows:
            hits = r.get("text_hits") or []
            got += [(r["video"], h["t"], h["t"] + 6.0) for h in hits] if hits else [(r["video"], r["start_s"], r["end_s"])]
        return got
    for t in data.get("topics", []):
        out.append({"id": t["id"], "kind": "topic", "question": t["title"],
                    "rows": [x for s in t["shots"] for x in rows_of(s["query"])]})
    for e in data.get("experiments", []):
        rows = rows_of(e["query"])
        if e["id"] == "f2-hide-in-water":            # only with stars showing: without them nobody is hiding
            rows = [r for r in rows if any(recs[r[0]]["count"][int(r[1] / STEP):int(r[2] / STEP)])]
        if e["id"].startswith("f4-first"):           # a first look is the first one only
            rows = rows[:1]
        out.append({"id": e["id"], "kind": "experiment", "question": e["try"], "rows": rows})
    return out


def answers_question(m, qs, rec):
    """The questions this moment answers on screen: the query matches its seconds (half of them, or 4 s), one of the
    question's events (NEEDS) is in it, and the lines say what it asks for (MUST_SAY). Experiments first. An impact or a
    shooting counts only where the moment's own label is sure of it, and a line that names one it isn't sure of proves
    nothing (PR 35): what it was mined as is a word nobody checked."""
    out = []
    kinds = (set(m["kinds"]) - {*IMPACT, "fall", "shooting"}) | {m["label"]["kind"]}
    for q in qs:
        need = NEEDS.get(q["id"])
        if not need or not need & kinds:
            continue
        if q["id"] in MUST_SAY:
            rx, before = MUST_SAY[q["id"]]
            if not any(rx.search(library.positive_text(s["notable"])) for s in rec["sheets"]
                       if launch.overlap(m["start_s"] - before, m["end_s"], s["t"], s["t"] + rec["every"]) > 0
                       and not launch.unverified_in(m, s, rec["every"])):
                continue
        share = max((launch.overlap(m["start_s"], m["end_s"], a, b) for v, a, b in q["rows"] if v == m["video"]), default=0.0)
        if share >= min(4.0, 0.5 * (m["end_s"] - m["start_s"])):
            out.append({"id": q["id"], "kind": q["kind"], "question": q["question"]})
    return sorted(out, key=lambda q: q["kind"])


def shelf_of(m):
    """"action" (a clear impact at the peak, a shootout, a chase or loud action that answers no question), "proof" (a
    question answered on screen, 10 s or more), else "list"."""
    long = m["end_s"] - m["start_s"] >= MAIN_MIN_S - 1e-6
    kind, sure = m["label"]["kind"], m["label"]["sure"]
    if m["family"] in SMALL and m["family"] != "first look":
        return "list"                              # (the impact next to a reaction or a find is a moment of its own)
    if long and sure and kind in ("crash", "explosion", "stunt", "fall", "shooting"):
        return "action"
    if m["family"] == "calm":
        return "list"
    if long and m.get("questions") and m["family"] in ("main", "first look"):
        return "proof"
    if not sure and m["main"] in (*IMPACT, "fall"):
        return "list"                              # an impact its own peak doesn't show (PR 35): look before it is used
    if long and m["family"] == "main" and (kind in ("chase", "star rise", "stars lost", "heat icons", "star look")
                                           or "loud" in m["kinds"] or "shooting" in m["kinds"] or "chase" in m["kinds"]
                                           or set(m["kinds"]) & (set(IMPACT) | {"fall"})):
        return "action"
    return "list"


# --- 5. Statuses ---

def status_spans(shorts_dir=None):
    """{"posted" | "rendered" | "rejected": [(stem, from, to, name)]} from _used_footage.json. An entry with no status
    counts as posted (locked): only an explicit "rendered" unlocks footage."""
    used = footage_short.load_used(shorts_dir or launch.SHORTS_DIR)
    out = {"posted": [], "rendered": [], "rejected": []}
    for s in used["shorts"]:
        key = "posted" if footage_short.locked(s) else "rendered"
        out[key] += [(v, float(a), float(b), s.get("name")) for v, a, b in s.get("spans", [])]
    for s in used.get("rejected", []):
        out["rejected"] += [(v, float(a), float(b), s.get("id")) for v, a, b in s.get("spans", [])]
    return out


def status_of(m, spans):
    """(status, the entry it comes from). Posted or rendered: half a second or more of the moment was shown. Rejected:
    half of it lies in a rejected moment (its neighbours stay free)."""
    def shared(rows):
        return [(launch.overlap(m["start_s"], m["end_s"], a, b), name) for v, a, b, name in rows if v == m["video"]]
    length = max(1e-6, m["end_s"] - m["start_s"])
    for status, least in (("posted", 0.5), ("rejected", 0.5 * length), ("rendered", 0.5)):
        rows = [(x, name) for x, name in shared(spans[status]) if x >= least]
        if rows:
            return status, max(rows, key=lambda r: r[0])[1]
    return "new", None


def refresh(bank, shorts_dir=None):
    """Every moment's status, read fresh from _used_footage.json."""
    spans = status_spans(shorts_dir)
    for m in bank["moments"]:
        m["status"], m["status_from"] = status_of(m, spans)
    bank["totals"] = totals(bank)
    return bank


# --- 6. The bank ---

def rank_key(m):
    """Best first: payoff, a sure label before an unsure one, new before rendered before the locked ones, points."""
    return (quiet(m), -m["payoff"]["tier"], not m["label"]["sure"], STATUSES.index(m.get("status", "new")), -m["score"],
            m["id"])


def covered_s(moments):
    """The seconds of recordings the moments cover, overlaps counted once."""
    total = 0.0
    for video in {m["video"] for m in moments}:
        end = -1.0
        for a, b in sorted((m["start_s"], m["end_s"]) for m in moments if m["video"] == video):
            total += max(0.0, b - max(a, end))
            end = max(end, b)
    return total


def totals(bank):
    ms = bank["moments"]
    return {"moments": len(ms), "covered_s": round(covered_s(ms), 1),
            "recorded_s": round(sum(r["duration_s"] for r in bank["recordings"]), 1),
            "shelves": {k: sum(1 for m in ms if m["shelf"] == k) for k in SHELVES},
            "calm": sum(1 for m in ms if quiet(m)), "covered_without_calm_s": round(covered_s([m for m in ms if not quiet(m)]), 1),
            "unsure": {k: sum(1 for m in ms if m["shelf"] == k and not m["label"]["sure"]) for k in SHELVES},
            "statuses": {s: sum(1 for m in ms if m.get("status") == s) for s in STATUSES}}


def frame_at(rec, t):
    """(sheet's file name, which of its 4 frames) that shows second t: the index's own pictures, so no ffmpeg."""
    every = rec["every"]
    k = max(0, min(len(rec["idx"].get("sheets", [])) - 1, int(t / every)))
    sheet = rec["idx"]["sheets"][k] if rec["idx"].get("sheets") else {"t": 0.0}
    return index.frame_name(sheet["t"]), min(3, max(0, int((t - sheet["t"]) / (every / 4))))


def quiet(m):
    """A calm stretch with nothing in it: at the bottom of the page, folded away."""
    return m["family"] == "calm" and m["shelf"] == "list"


def pictures(m, rec):
    """3 pictures: the moment's start, its peak and its end."""
    out = []
    for t in (m["start_s"] + 0.3, m["peak_s"], max(m["start_s"], m["end_s"] - 0.8)):
        name, frame = frame_at(rec, t)
        out.append({"t": round(t, 1), "sheet": name, "frame": frame})
    return out


def make_moments(recs, checker=None, cache_dir=None, work=None, shotlist=None, log=log_line):
    """(every moment of these recordings, labelled and shelved; those that may not be shown, with why; what was left out;
    model calls). Reads only saved data, but for a peak nobody has looked at (checker None: those stay unsure)."""
    cache_dir = Path(cache_dir or index.LIBRARY / "_cache")
    firsts = first_looks(recs)
    qs = questions(recs, shotlist)
    moments, dropped, left, calls, t0 = [], [], {}, 0, time.perf_counter()
    for n, rec in enumerate(recs.values(), 1):
        found, out = mine_all(rec, firsts)
        for k, v in out.items():
            left[k] = left.get(k, 0) + v
        answers = saved_answers(cache_dir, rec["stem"])
        asked = 0
        for m in found:
            sheets, c = look_at(m, answers, checker, work)
            asked += c
            why = blocked(m, rec, sheets)
            if why:
                dropped.append({"id": m["id"], "video": m["video"], "start_s": m["start_s"], "end_s": m["end_s"],
                                "why": why})
                continue
            set_label(m, rec, sheets)
            m["questions"] = answers_question(m, qs, rec)
            m["shelf"] = shelf_of(m)
            m["pictures"] = pictures(m, rec)
            moments.append(m)
        calls += asked
        log(f"  {n}/{len(recs)} {rec['stem'][-14:]}: {len(found)} moments, {asked} new look(s) at a peak, "
            f"{time.perf_counter() - t0:.0f} s so far")
    return moments, dropped, left, calls


def build(lib_dir=None, shorts_dir=None, bank_dir=None, checker=None, shotlist=None, stems=None, hud6=None,
          recs=None, log=log_line):
    """Mine every recording, label, shelve, read the statuses, write content_bank.json and bank.html. Returns the bank."""
    lib_dir, bank_dir = Path(lib_dir or index.LIBRARY), Path(bank_dir or BANK)
    shorts_dir = Path(shorts_dir or launch.SHORTS_DIR)
    bank_dir.mkdir(parents=True, exist_ok=True)
    recs = recs if recs is not None else load_recs(lib_dir, shorts_dir, bank_dir, stems, hud6, log)
    moments, dropped, left, calls = make_moments(recs, checker, lib_dir / "_cache", bank_dir / "peaks", shotlist, log)
    posted = launch.load_posted(launch.POSTED_FILE, log=log)
    for m in moments:
        m["repeats"] = launch.topic_repeats(m, posted, launch.moment_text(recs[m["video"]], m))[1]
    bank = {"version": VERSION, "built": datetime.now().isoformat(timespec="seconds"), "library": str(lib_dir),
            "recordings": [{"stem": r["stem"], "path": r["path"], "duration_s": round(r["duration_s"], 2),
                            "mode": r["mode"], "profile": r["profile"],
                            "usable_s": round(sum(b - a for a, b in r["spans"]), 1)} for r in recs.values()],
            "moments": moments, "never_used": dropped, "left_out": left, "vision_calls": calls}
    refresh(bank, shorts_dir)
    bank["moments"].sort(key=rank_key)
    write(bank, bank_dir)
    log(f"Bank: {bank['totals']['moments']} moments in {bank_dir / BANK_FILE}")
    return bank


def write(bank, bank_dir=None):
    bank_dir = Path(bank_dir or BANK)
    index.write_json(bank_dir / BANK_FILE, bank)
    (bank_dir / PAGE_FILE).write_text(page_text(bank, bank_dir), encoding="utf-8")
    return bank_dir / PAGE_FILE


def new_recordings(input_dir=None, lib_dir=None):
    """The GTA 6 recordings in the input folder that have no index yet, oldest first."""
    lib = index.read_json(Path(lib_dir or index.LIBRARY) / "library.json") or {}
    out = []
    for v in sorted(Path(input_dir or launch.INPUT).glob(launch.GAME_FILES[GAME] + "*")):
        if v.suffix.lower() in index.VIDEO_EXTS and v.stem not in (lib.get("videos") or {}) and not index.off_limits(v):
            out.append(v)
    return out


def index_new(input_dir=None, lib_dir=None, bank_dir=None, log=log_line, index_video=None, clip_runner=None,
              guided_run=None, check=True):
    """Index the GTA 6 recordings that have no index, the way /launch prepare does (launch.index_recording: the HUD
    checked against the model, NO-HUD where it can't be trusted), with clip.py alongside. One under 15 s is left out.
    Returns [{"recording", "duration_s", "mode", "vision_calls", "seconds"}]."""
    lib_dir, bank_dir = Path(lib_dir or index.LIBRARY), Path(bank_dir or BANK)
    durations = {v.stem: launch.probe_duration(v) for v in new_recordings(input_dir, lib_dir)}
    todo = [v for v in new_recordings(input_dir, lib_dir) if durations[v.stem] >= launch.MIN_RECORDING_S]
    if not todo:
        log("No new recordings to index.")
        return []
    if check:
        launch.preflight(index.MODEL, log)
    choice = launch.choose_profile(GAME, GAME, todo, durations, launch.GAMES[GAME]["slots"], log, guided_run)
    log(f"Indexing {len(todo)} new recording(s), {sum(durations[v.stem] for v in todo) / 60:.1f} min, with "
        f"{choice['profile']}: {choice['why']}")
    runner = clip_runner or (lambda v, p: launch.run_clip(v, p, bank_dir / "clip_runs", bank_dir / "logs"))
    pool, out = concurrent.futures.ThreadPoolExecutor(max_workers=1), []
    try:
        jobs = [pool.submit(runner, v, choice["profile"]) for v in todo]
        for v in todo:
            t = time.perf_counter()
            idx, mode, _ = launch.index_recording(v, choice, GAME, index.EVERY_S, lib_dir, None,
                                                  index_video or index.index_video, log, log)
            out.append({"recording": v.stem, "duration_s": round(durations[v.stem], 1), "mode": mode,
                        "vision_calls": (idx.get("timing_s") or {}).get("model_calls", 0),
                        "seconds": round(time.perf_counter() - t, 1)})
        for j in jobs:
            j.result()
    finally:
        pool.shutdown(wait=True)
    return out


# --- 7. The picker reads the bank (launch.prepare, launch.repick) ---

def candidates(moments, recs, spans=None, offered=("new", "rendered")):
    """The bank's action and proof moments /launch may pick from, as the picker's moments (launch.pick_step): those
    whose status is new or rendered (rendered ones cost RENDERED_COST points and rank after new ones of the same
    payoff); posted and rejected ones are never offered. A proof moment of a topic with facts carries its topic. spans:
    status_spans() (or the locked footage as of an older run)."""
    out = []
    for m in moments:
        rec = recs.get(m["video"])
        if rec is None or m["shelf"] not in ("action", "proof"):
            continue
        status = status_of(m, spans)[0] if spans is not None else m.get("status", "new")
        if status not in offered:
            continue
        lo, hi = m["span"]
        for v, a, b, _ in ((spans or {}).get("posted", []) + (spans or {}).get("rejected", [])):
            if v == m["video"] and lo < b and a < hi:         # a Short may not grow into locked footage
                if b <= m["start_s"] + 0.5:
                    lo = max(lo, b)
                elif a >= m["end_s"] - 0.5:
                    hi = min(hi, a)
        p = {k: m[k] for k in ("video", "path", "profile", "mode", "start_s", "end_s", "span", "session_s", "score",
                               "category", "peak_s", "shelf", "kinds", "label")}
        p.update(bank_id=m["id"], status=status, span=[round(lo, 2), round(hi, 2)], terms=dict(m["terms"]), session_s=round(
            rec["offset_s"] + (m["start_s"] + m["end_s"]) / 2, 1), questions=m.get("questions", []))
        # (the HUD change nearest the peak is where a topic's proof is; launch.anchor reads topic["at_s"])
        topic = next((q["id"] for q in m.get("questions", []) if q["id"] in launch.TOPIC_FACTS), None)
        hud = {"heat icons": "heat-icons", "star look": "star-looks"}
        topic = next((hud[k] for k in ("heat icons", "star look") if k in m["kinds"]), topic)
        if m["shelf"] == "proof" and topic:
            at = min((e["t0"] for e in m["events"] if e["kind"] in HUD_KINDS), key=lambda t: abs(t - m["peak_s"]),
                     default=m["peak_s"])
            p["topic"] = {"id": topic, "shot": "bank", "why": m["label"]["why"], "at_s": round(at, 2),
                          "facts": launch.TOPIC_FACTS.get(topic, [])}
            p["terms"]["topic"] = launch.TOPIC_BONUS
            if {"heat icons", "star look"} & set(m["kinds"]):
                p["terms"]["hud6"] = 1.0
            p["category"] = f"topic: {topic}"
        if status == "rendered":
            p["terms"]["rendered"] = -RENDERED_COST
        p["score"] = round(sum(v for v in p["terms"].values() if isinstance(v, (int, float))), 3)
        impact = impact_of(m)
        if impact:                                          # the sheets at the peak show it, whatever words they used
            for s in rec["sheets"]:
                if s["t"] - 1.0 <= m["peak_s"] < s["t"] + rec["every"] + 1.0 and impact not in s["kinds"]:
                    s["kinds"] = sorted(set(s["kinds"]) | {impact})
        out.append(p)
    # Moments of different families may overlap in the bank; two candidates of one night may not.
    best = []
    for p in sorted(out, key=lambda p: (-launch.payoff_strength(p)[0], -p["score"], p["session_s"])):
        if not any(q["video"] == p["video"] and launch.overlap(p["start_s"], p["end_s"], q["start_s"], q["end_s"]) > 2.0
                   for q in best):
            best.append(p)
    return sorted(best, key=lambda p: (-p["score"], p["session_s"]))


def keep_what_the_peak_shows(m):
    """After the safety checks of a candidate from the bank: an impact seen somewhere else in its footage (M24's parked
    pickup, hit 4 s before the jump) doesn't name it. Of m["verified"], only the impacts seen round the peak stay, with the
    one its label is sure of; a moment whose label is unsure keeps none (PR 35): its own peak doesn't show one."""
    peak, v = m.get("peak_s"), m.get("verified")
    if peak is None or v is None:
        return m
    unsure = "label" in m and not m["label"].get("sure")
    for k in IMPACT:
        v[k] = [] if unsure else [s for s in v.get(k, []) if s[0] < peak + AT_PEAK_S[1] and peak - AT_PEAK_S[0] < s[1]]
        if not v[k]:
            v.pop(k)
    impact = impact_of(m)
    if impact:
        v.setdefault(impact, []).append([round(peak - 1.0, 2), round(peak + 1.0, 2)])
        m["terms"][impact] = launch.EVENTS[impact][0]
    return m


# --- 8. Mark a moment posted or rejected; the migration ---

def backup(paths, bank_dir=None):
    """Copies of these files in bank\\backups\\<time>, before anything is changed. Returns the folder."""
    base = Path(bank_dir or BANK) / "backups" / f"{datetime.now():%Y%m%d-%H%M%S}"
    folder, k = base, 1
    while folder.exists():
        k += 1
        folder = base.with_name(f"{base.name}-{k}")
    folder.mkdir(parents=True)
    for p in paths:
        if Path(p).is_file():
            shutil.copy2(p, folder / Path(p).name)
    return folder


def new_topics(topic, path=None):
    """(a temp file next to posted_topics.json with one topic added, every topic in it): not a byte of the ones already
    there is touched, every earlier topic must still be there word for word, and the picker must be able to read it
    (launch.load_posted) before it may take the list's place. Otherwise BankError, and no temp file."""
    path = Path(path or launch.POSTED_FILE)
    text = path.read_text(encoding="utf-8")
    before = json.loads(text)
    if any(t.get("name") == topic["name"] for t in before["topics"]):
        raise BankError(f"posted_topics.json already has a topic called {topic['name']!r}: give another --name")
    at = text.rstrip().rfind("]")
    head, tail = text[:at].rstrip(), text[at:]
    new = head + ("," if before["topics"] else "") + "\n  " + json.dumps(topic, ensure_ascii=False) + "\n " + tail
    after = json.loads(new)
    if after["topics"][:-1] != before["topics"] or after["topics"][-1] != topic or after.get("about") != before.get("about"):
        raise BankError("posted_topics.json would not read back the same: nothing was written")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(new, encoding="utf-8", newline="")
    try:
        launch.load_posted(tmp, log=lambda m: None)
    except launch.LaunchError as e:
        tmp.unlink()
        raise BankError(f"the topic {topic['name']!r} can't be added, so nothing was written: {e}") from None
    return tmp, after["topics"]


def add_topic(topic, path=None):
    """Append one topic to posted_topics.json (new_topics: checked before anything is replaced)."""
    path = Path(path or launch.POSTED_FILE)
    tmp, topics = new_topics(topic, path)
    tmp.replace(path)
    return topics


STOP = set("a an the and or of on in at to with near then past into over by from for is are his her their its player "
           "players character characters walks walking drives driving runs running rides stands standing man woman men "
           "people two one other others visible street streets city road while after through toward towards".split())


def topic_words(text, n=3):
    """A few telling words of a line, for a posted topic's rule."""
    out = []
    for w in re.findall(r"[a-z]{4,}", (text or "").lower()):
        if w not in STOP and w not in out:
            out.append(w)
    return out[:n]


def shorts_of(shorts_dir, bank_id):
    """The finished Shorts /launch made of a moment: [(entry name in _used_footage.json, what it showed, title)]."""
    out = []
    for f in sorted(Path(shorts_dir).glob("*/" + launch.RENDER_LOG)):
        log_ = index.read_json(f) or {}
        out += [(f"{Path(e['folder']).name}@{f.parent.name}", [list(s) for s in e["shown"]], e.get("title"))
                for e in log_.get("shorts", []) if e.get("bank_id") == bank_id and e.get("status") == "READY"
                and e.get("shown") and e.get("folder") and not log_.get("dry_run")]
    return out


def type_of_short(folder, entry):
    """The type of a finished Short in the posted list's words, from its pick in the run's candidates.json, or None."""
    cand = index.read_json(Path(folder).parent / "candidates.json") or {}
    pick = next((p for p in cand.get("picks", []) if p.get("short") == entry.get("short")
                 or (entry.get("moment") and p.get("id") == entry["moment"])), None)
    return launch.plain_type(launch.moment_type(pick)) if pick else None


def mark_posted(what, name=None, kind=None, words_=None, on=None, url=None, shorts_dir=None, bank_dir=None,
                posted_file=None, log=log_line):
    """The owner posted a Short of this moment (a bank id, or a finished Short's folder): its footage is locked
    (_used_footage.json, status "posted") and its topic is added to posted_topics.json, as the M24 entry was by hand.
    By id: when /launch made a Short of the moment, all that Short showed is locked (a Short shows more than the moment:
    up to 35 s), else the moment's own seconds. Both files are copied to bank\\backups first; nothing already in them
    changes."""
    shorts_dir, bank_dir = Path(shorts_dir or launch.SHORTS_DIR), Path(bank_dir or BANK)
    posted_file = Path(posted_file or launch.POSTED_FILE)
    on = on or date.today().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", on):
        raise BankError("--on is a day: YYYY-MM-DD")
    bank = read_bank(bank_dir) or {"moments": [], "recordings": []}
    m = next((x for x in bank["moments"] if x["id"] == what), None)
    text, title = "", None
    if m is not None:
        made = shorts_of(shorts_dir, m["id"])
        locks = [(n, s) for n, s, _ in made] or [(f"{m['id']}@bank", [[m["video"], m["start_s"], m["end_s"]]])]
        text, title = m["label"]["text"], made[-1][2] if made else name
        kind = kind or launch.plain_type(launch.moment_type(dict(m, point=None)) or m["label"]["kind"])
    else:
        folder = Path(what)
        log_ = index.read_json(folder.parent / launch.RENDER_LOG) or {}
        e = next((x for x in log_.get("shorts", []) if x.get("folder") and Path(x["folder"]).name == folder.name), None)
        if e is None or not e.get("shown"):
            raise BankError(f"no moment in the bank has the id {what!r}, and it is not a finished Short's folder")
        locks = [(f"{folder.name}@{folder.parent.name}", [list(s) for s in e["shown"]])]
        title, text = e.get("title"), e.get("title") or ""
        m = next((x for x in bank["moments"] if x["id"] == e.get("bank_id")), None)
        kind = kind or (launch.plain_type(launch.moment_type(dict(m, point=None))) if m else type_of_short(folder, e))
        if not kind:
            raise BankError("say what kind of Short it was: --type crash (or stunt, star rise, discovery, chase ...): the "
                            "picker doesn't follow a Short with one of the same type")
    name = name or title
    if not name:
        raise BankError("say what it was: --name \"can you survive jumping off an overpass\"")
    words_ = [w.lower() for w in (words_ or topic_words(text + " " + name))]
    if not words_:
        raise BankError("give a word or two that name it: --words overpass jump")
    rules = [{"all_words": words_}]
    if kind in launch.IMPACT_TERMS:
        rules.insert(0, {"term": [kind], "words": words_})
    topic = {"name": name, "type": kind, "posted": on, "added": datetime.now().isoformat(timespec="seconds"),
             "rules": rules}
    used_file = shorts_dir / footage_short.USED_FOOTAGE
    saved = backup([used_file, posted_file], bank_dir)
    # The topic is checked, then the footage locked, then the topic written: whatever fails, posted footage is never free.
    tmp, _ = new_topics(topic, posted_file)
    try:
        for entry_name, spans in locks:
            footage_short.set_status(shorts_dir, entry_name, spans, "posted", posted=on,
                                     note="posted by the owner" + (f": {url}" if url else ""))
        tmp.replace(posted_file)
    except OSError as e:
        raise BankError(f"{name!r} is not all written down yet ({type(e).__name__}: {e}); run the same command again. Copies of "
                        f"both files as they were: {saved}") from None
    finally:
        tmp.unlink(missing_ok=True)
    if bank["moments"]:
        write(refresh(bank, shorts_dir), bank_dir)
    spans = [s for _, sp in locks for s in sp]
    log(f"Posted: {name} ({on}). {len(spans)} span(s) locked as {', '.join(n for n, _ in locks)}; topic added to "
        f"{posted_file.name}. Copies of both files as they were: {saved}")
    return topic, spans


def mark_rejected(what, why=None, shorts_dir=None, bank_dir=None, log=log_line):
    """The owner said no to this moment: never offered again (a "rejected" list in _used_footage.json, next to the
    Shorts)."""
    shorts_dir, bank_dir = Path(shorts_dir or launch.SHORTS_DIR), Path(bank_dir or BANK)
    bank = read_bank(bank_dir) or {"moments": []}
    m = next((x for x in bank["moments"] if x["id"] == what), None)
    if m is None:
        raise BankError(f"no moment in the bank has the id {what!r}")
    used_file = shorts_dir / footage_short.USED_FOOTAGE
    saved = backup([used_file], bank_dir)
    used = footage_short.load_used(shorts_dir)
    used["rejected"] = [r for r in used.get("rejected", []) if r.get("id") != what] + [
        {"id": what, "spans": [[m["video"], m["start_s"], m["end_s"]]], "on": date.today().isoformat(),
         "label": m["label"]["name"], **({"why": why} if why else {})}]
    index.write_json(used_file, used)
    write(refresh(bank, shorts_dir), bank_dir)
    log(f"Rejected: {what} ({m['label']['name']}). Copy of the file as it was: {saved}")
    return used["rejected"][-1]


# The entries of Nov 24's _used_footage.json that were posted but don't say so in a field: (a piece of the entry's name,
# the posted topic it was posted as). The rest without a posted day were rendered and not posted.
POSTED_AS = (("my-last-ride-in-gta-5", "last ride in GTA 5"), ("how-gta-6-cops-know-you-did-a-crime", "crime reported"),
             ("crash-2026-11-20@manual", "black SUV crash on the bridge"),
             ("this-gta-6-motorbike-ride-ends-badly", "motorbike crash (Short 6)"),
             ("effigy-2026-11-20@manual", "tree effigy / passing out"))


def migrate(shorts_dir=None, bank_dir=None, posted_file=None, log=log_line):
    """Once: every entry of _used_footage.json gets a "status". "posted" when the entry has a posted day or is one of
    POSTED_AS (its topic has a posted day in posted_topics.json: that day is added); else "rendered". No entry, span,
    day or note is lost or changed; entries that have a status already are left alone. Returns (before, after, backup)."""
    shorts_dir, bank_dir = Path(shorts_dir or launch.SHORTS_DIR), Path(bank_dir or BANK)
    used_file = shorts_dir / footage_short.USED_FOOTAGE
    posted_file = Path(posted_file or launch.POSTED_FILE)
    before = footage_short.load_used(shorts_dir)
    topics = {t["name"]: t for t in (index.read_json(posted_file) or {}).get("topics", [])}
    saved = backup([used_file, posted_file], bank_dir)
    after = json.loads(json.dumps(before))
    for s in after["shorts"]:
        if s.get("status"):
            continue
        as_topic = next((topic for piece, topic in POSTED_AS if piece in s.get("name", "")), None)
        day = s.get("posted") or (topics.get(as_topic) or {}).get("posted")
        if day:
            s["status"], s["posted"] = "posted", day
            if as_topic and "posted" not in next(x for x in before["shorts"] if x["name"] == s["name"]):
                s["posted_as"] = as_topic
        else:
            s["status"] = "rendered"
    kept = [{k: v for k, v in s.items() if k in b} for s, b in zip(after["shorts"], before["shorts"])]
    if len(after["shorts"]) != len(before["shorts"]) or kept != before["shorts"]:
        raise BankError("the migration would change or lose an entry: nothing was written")
    index.write_json(used_file, after)
    for s in after["shorts"]:
        log(f"  {s['status']:9s} {s.get('posted') or '':10s} {s['name']}" + (f" (posted as \"{s['posted_as']}\")"
                                                                             if s.get("posted_as") else ""))
    log(f"{len(before['shorts'])} entries before, {len(after['shorts'])} after; the files as they were: {saved}")
    return before, after, saved


# --- 9. The review page ---

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Content bank</title>
<style>
:root {{ --bg:#f6f7f8; --card:#fff; --ink:#1b1f24; --soft:#5d6773; --line:#d9dee4; --good:#1f7a4d; --warn:#9a5b00; --bad:#b3261e; --chip:#eef1f4; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#14171a; --card:#1d2125; --ink:#e8ebee; --soft:#9aa5b1; --line:#333a42; --good:#5fc48f; --warn:#e0a458; --bad:#ef8a83; --chip:#2a3037; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 "Segoe UI",system-ui,sans-serif; }}
main {{ max-width:1120px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:26px; margin:0 0 4px; }} h2 {{ font-size:20px; margin:36px 0 4px; }}
p, li {{ max-width:75ch; }} .soft {{ color:var(--soft); }}
.totals {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin:16px 0; }}
.totals div {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 12px; }}
.totals b {{ display:block; font-size:22px; font-variant-numeric:tabular-nums; }}
.m {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:12px; margin:10px 0; }}
.m.posted, .m.rejected {{ opacity:.6; }}
.top {{ display:flex; flex-wrap:wrap; gap:6px 10px; align-items:baseline; }}
.top b {{ font-size:16px; }}
.chip {{ background:var(--chip); border-radius:5px; padding:1px 8px; font-size:13px; white-space:nowrap; }}
.chip.new {{ color:var(--good); }} .chip.rendered {{ color:var(--warn); }} .chip.posted, .chip.rejected, .chip.unsure {{ color:var(--bad); }}
.pics {{ display:grid; grid-template-columns:repeat(3,1fr); gap:6px; margin:8px 0; max-width:780px; }}
.pics a {{ display:block; aspect-ratio:16/9; background-size:200% 200%; border-radius:5px; background-color:#000; }}
.why {{ font-size:14px; color:var(--soft); margin:2px 0; }}
code {{ font:13px Consolas,monospace; background:var(--chip); border-radius:4px; padding:1px 5px; overflow-wrap:anywhere; }}
nav a {{ margin-right:14px; }} a {{ color:inherit; }}
button {{ font:inherit; font-size:13px; border:1px solid var(--line); background:var(--chip); color:inherit; border-radius:5px; padding:2px 8px; cursor:pointer; }}
details {{ margin-top:6px; }} summary {{ cursor:pointer; color:var(--soft); font-size:14px; }}
</style></head><body><main>
<h1>Content bank</h1>
<p class="soft">Built {built} from {recordings} recordings.</p>
<div class="totals">{totals}</div>
<h2>What to do</h2>
<ol>
<li>Pick a shelf below. The best moments are at the top of each one.</li>
<li>Click a picture to play the recording from that second.</li>
<li>After you post a Short that <b>/launch made</b>: use the line under "Posted it?" on its review page. After you post one you <b>made yourself</b> from a moment here: open the moment's <b>Mark it</b> line, press <b>Copy</b> next to "Posted", change the words in quotes to what the Short was about, and paste it in PowerShell (in <code>C:\\AI\\gta-clipper</code>). That footage is then locked.</li>
<li>If you never want to see a moment again: copy the "Rejected" line instead.</li>
<li><b>unsure</b> means the pictures at the loudest second did not show what the moment was found for. Look before you use it.</li>
</ol>
<nav>{nav}</nav>
{shelves}
</main>
<script>
document.addEventListener("click", function (e) {{
  var b = e.target.closest("button[data-copy]"); if (!b) return;
  navigator.clipboard.writeText(b.getAttribute("data-copy")).then(function () {{ b.textContent = "Copied"; }});
}});
</script>
</body></html>
"""


def esc(x):
    return html.escape(str(x if x is not None else ""), quote=True)


def clock(s):
    return f"{int(s) // 60}:{int(s) % 60:02d}"


def rel(path, bank_dir):
    return quote(Path(os.path.relpath(path, bank_dir)).as_posix())


def moment_html(m, bank, bank_dir):
    lib = Path(bank["library"])
    pos = ("0% 0%", "100% 0%", "0% 100%", "100% 100%")
    video = rel(m["path"], bank_dir)
    pics = "".join(
        f'<a href="{video}#t={p["t"]:.0f}" target="_blank" title="{clock(p["t"])}" aria-label="Play from {clock(p["t"])}" '
        f'style="background-image:url(\'{rel(lib / m["video"] / "frames" / p["sheet"], bank_dir)}\');'
        f'background-position:{pos[p["frame"]]}"></a>' for p in m["pictures"])
    label, unsure = m["label"], not m["label"]["sure"]
    py = r".\.venv\Scripts\python.exe bank.py"
    posted = f'{py} posted {m["id"]} --name "what the Short was about"'
    rejected = f"{py} rejected {m['id']}"
    qs = "".join(f'<p class="why">Answers: {esc(q["question"])} <span class="soft">({esc(q["id"])})</span></p>'
                 for q in m.get("questions", [])[:3])
    repeats = f'<p class="why">Repeats a posted topic: {esc(", ".join(m["repeats"]))}</p>' if m.get("repeats") else ""
    conf = "unsure" if unsure else f"{label['confidence']:.0%} sure"
    name = label["text"] if unsure else f"{label['name']}: {label['text']}"
    return (f'<article class="m {m["status"]}" id="{esc(m["id"])}"><div class="top"><b>{esc(name)}</b>'
            f'<span class="chip {"unsure" if unsure else ""}">{esc(conf)}</span>'
            f'<span class="chip">payoff {m["payoff"]["tier"]}/3</span>'
            f'<span class="chip">{m["end_s"] - m["start_s"]:.0f} s</span>'
            f'<span class="chip {m["status"]}">{m["status"]}</span></div>'
            f'<div class="pics">{pics}</div>'
            f'<p class="why">{esc(m["video"][-14:])} {clock(m["start_s"])}-{clock(m["end_s"])}, peak {clock(m["peak_s"])} '
            f'({esc(m["peak_by"])}) &middot; payoff: {esc(m["payoff"]["why"])} &middot; {m["score"]:.1f} points</p>'
            f'<p class="why">Label: {esc(label["why"])}</p>{qs}{repeats}'
            f'<details><summary>Mark it ({esc(m["id"])})</summary>'
            f'<p>Posted: <code>{esc(posted)}</code> <button data-copy="{esc(posted)}">Copy</button></p>'
            f'<p>Rejected: <code>{esc(rejected)}</code> <button data-copy="{esc(rejected)}">Copy</button></p></details>'
            f'</article>')


def page_text(bank, bank_dir):
    t = bank["totals"]
    cells = [(t["shelves"][k], SHELVES[k] + (f" ({t['unsure'][k]} unsure)" if t["unsure"][k] else "")) for k in SHELVES]
    cells += [(f"{t['covered_s'] / 60:.0f} of {t['recorded_s'] / 60:.0f}", "minutes of footage in the bank, of all recorded "
               f"({t['covered_without_calm_s'] / 60:.0f} without the calm stretches)"),
              (t["statuses"]["new"], "new"), (t["statuses"]["rendered"], "rendered, not posted"),
              (t["statuses"]["posted"], "posted (locked)"), (t["statuses"]["rejected"], "rejected")]
    blurb = {"action": "Crashes, jumps, shootouts, chases. 10 seconds or more.",
             "proof": "Moments that answer one of your experiments or shot-list questions on screen.",
             "list": "Short facts for \"5 things\" Shorts. Not rendered by /launch yet."}
    shelves = ""
    for k, title in SHELVES.items():
        rows = sorted((m for m in bank["moments"] if m["shelf"] == k), key=rank_key)
        calm = [m for m in rows if quiet(m)]
        shelves += (f'<section id="{k}"><h2>{esc(title)} ({len(rows)})</h2><p class="soft">{blurb[k]}</p>'
                    + ("".join(moment_html(m, bank, bank_dir) for m in rows if not quiet(m)) or "<p>Nothing yet.</p>")
                    + (f"<details><summary>{len(calm)} calm stretches (driving, walking, boats, flying: nothing happens, "
                       "good as background)</summary>" + "".join(moment_html(m, bank, bank_dir) for m in calm) + "</details>"
                       if calm else "") + "</section>")
    return PAGE.format(built=esc(bank["built"].replace("T", " ")), recordings=len(bank["recordings"]),
                       totals="".join(f"<div><b>{esc(n)}</b>{esc(what)}</div>" for n, what in cells),
                       nav="".join(f'<a href="#{k}">{esc(v)}</a>' for k, v in SHELVES.items()), shelves=shelves)


# --- The command ---

def make_checker(log=log_line):
    """The safety question through Ollama, or None (with a note) when it isn't running: labels nobody has looked at
    stay unsure then."""
    try:
        return safety.Checker(launch.GAMES[GAME]["name"], index.LIBRARY / "_cache", log=log)
    except vlm.VlmError as e:
        log(f"Note: no new looks at peaks ({e}); labels without a saved answer stay unsure")
        return None


def main():
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="The content bank: every usable moment of your GTA 6 recordings.")
    sub = ap.add_subparsers(dest="do", required=True)
    b = sub.add_parser("build", help="index what is new, mine everything, label it, write the bank and its page")
    b.add_argument("--no-index", action="store_true", help="leave recordings without an index out")
    b.add_argument("--no-model", action="store_true", help="ask the vision model nothing (saved answers only)")
    sub.add_parser("page", help="write bank.html again with today's statuses")
    p = sub.add_parser("posted", help="you posted a Short of this moment: lock its footage, add its topic")
    p.add_argument("what", help="the moment's id on the page, or a finished Short's folder")
    p.add_argument("--name", help="what the Short was about, in a few words")
    p.add_argument("--type", help="crash, stunt, star rise, discovery ... (default: the moment's own)")
    p.add_argument("--words", nargs="+", help="words that name it, for spotting repeats (default: from its label)")
    p.add_argument("--on", help="the day it went up, YYYY-MM-DD (default today)")
    p.add_argument("--url", help="its YouTube address")
    r = sub.add_parser("rejected", help="never offer this moment again")
    r.add_argument("what", help="the moment's id on the page")
    r.add_argument("--why", help="a few words")
    sub.add_parser("migrate", help="once: give every entry of _used_footage.json a status")
    args = ap.parse_args()
    fastff.use()
    try:
        if args.do == "build":
            with heavy.job("bank.py build", log=log_line):
                if not args.no_index:
                    for row in index_new():
                        log_line(f"  indexed: {row}")
                checker = None if args.no_model else make_checker()
                try:
                    build(checker=checker)
                finally:
                    if checker is not None:
                        vlm.unload(checker.model)
        elif args.do == "page":
            bank = read_bank()
            if not bank:
                raise BankError("there is no bank yet: run bank.py build")
            print(write(refresh(bank)))
        elif args.do == "posted":
            mark_posted(args.what, args.name, args.type, args.words, args.on, args.url)
        elif args.do == "rejected":
            mark_rejected(args.what, args.why)
        else:
            migrate()
    except (BankError, launch.LaunchError, index.IndexError_, safety.SafetyError, heavy.Busy) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
