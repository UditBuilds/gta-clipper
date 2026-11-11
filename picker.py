"""Pick a Short's pictures by what they show, from the tags tag_media.py saved.

In a script, a shot may ask for a picture instead of naming a file:

  {"beat": "full", "query": "swamp airboat", "region": "Grassrivers"}

and the picker finds the best tagged picture. A file path still works and is left alone. The rules:

- The query's words are matched against the picture's tags (main subject, subjects, names, setting, time).
- A picture is never used twice in one Short. The six cuts of one wallpaper count as one picture.
- Pictures used in the last 5 Shorts are avoided while another good match exists
  (the log is C:\\AI\\gta\\shorts\\_used_media.json).
- A picture with big text or a logo on it is used only when the shot says "text_ok": true. One with an age-rating
  card is never used, and a video starts after its rating card.
- A full-screen shot prefers a phone-shaped picture, so less of it is cropped away.
- If nothing matches well, the pick is refused and the closest match is named. Nothing unrelated is picked quietly.
- A label that names a region ("MOUNT KALAGA") is allowed only on a picture from that region's folder whose subjects
  don't clearly contradict it (regions.json).

Standard library only.

Usage:  python picker.py script.json     shows what would be picked for each shot, without rendering anything
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import tag_media

HERE = Path(__file__).resolve().parent
GTA_ROOT = Path(r"C:\AI\gta")                    # explainer.py's root: a script's paths are relative to it
MEDIA_PREFIX = "rockstar/"                       # where the tagged media is, inside GTA_ROOT
USED_LOG = GTA_ROOT / "shorts" / "_used_media.json"
REGIONS_FILE = HERE / "regions.json"
RECENT_SHORTS = 5                                # pictures of this many Shorts back are avoided

QUERY_KEYS = {"query", "region", "text_ok", "media", "type"}     # what a shot may add to ask for a picture
MEDIA_KINDS = ("image", "video", "any")
# How much a query word counts by where it is found. A word found only in the description is half a match: the
# description is a sentence, and a word in it may be a detail in the background.
WEIGHTS = {"main": 3.0, "names": 3.0, "subjects": 2.0, "region": 2.0, "setting": 1.5, "time": 1.5, "type": 1.0}
DESCRIPTION_WEIGHT = 0.5
STOP_WORDS = {"a", "an", "the", "of", "in", "on", "at", "with", "and", "or", "to", "from", "by", "for", "over", "near"}
SAME_WORDS = [{"cop", "police", "policeman", "officer"}, {"gator", "alligator", "crocodile"},
              {"chopper", "helicopter"}, {"motorcycle", "motorbike", "motorcyclist", "bike", "scooter"},
              {"plane", "airplane", "aeroplane", "aircraft", "seaplane", "jet"},
              {"gun", "pistol", "rifle", "shotgun", "revolver"}, {"skyline", "skyscraper", "cityscape"},
              {"sea", "ocean"}, {"wood", "forest"}, {"lake", "river"}, {"cash", "money"}, {"club", "nightclub"},
              {"dusk", "sunset", "sunrise"}]
# The shapes a shot likes, best first. "full" fills the 9:16 screen, a card is square unless it says otherwise, and a
# compare's two cards are 16:9.
SHAPE_ORDER = {"full": ("portrait", "phone", "square", "landscape", "ultrawide"),
               "square": ("square", "landscape", "portrait", "ultrawide", "phone"),
               "wide": ("landscape", "ultrawide", "square", "portrait", "phone"),
               "any": ()}
SHAPE_BONUS = (1.0, 0.75, 0.4, 0.15, 0.0)        # less than any tag match, so shape only decides between equals


class PickError(Exception):
    """A script asks for something the picker can't read (a bad region, a query on a lock-on, ...)."""


# --- Words ---

def stem(word):
    """A word as the picker compares it: lowercase, without a plural s."""
    word = word.lower()
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def words_of(text):
    return [stem(w) for w in re.findall(r"[a-z0-9]+", str(text).lower())]


def query_words(query):
    return [w for w in words_of(query) if w not in STOP_WORDS]


def same_word(wanted, found):
    """Does a query word match a tag word? The same word, a listed synonym, or the end of a joined word ("boat" finds
    "airboat" and "speedboat")."""
    if wanted == found:
        return True
    if any(wanted in group and found in group for group in SAME_WORDS):
        return True
    return len(wanted) >= 4 and len(found) > len(wanted) and found.endswith(wanted)


# --- Regions ---

def load_regions(path=REGIONS_FILE):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["regions"]


def plain(text):
    return " ".join(re.findall(r"[a-z0-9]+", str(text).lower()))


def find_region(text, regions):
    """The region a label or a "region" setting names, or None. The longest alias wins."""
    padded = f" {plain(text)} "
    best = None
    for region in regions:
        for alias in [region["name"], *region["aliases"]]:
            if f" {plain(alias)} " in padded and (best is None or len(alias) > best[0]):
                best = (len(alias), region)
    return best[1] if best else None


def region_label_problem(label, rel, entry, regions):
    """Why a label that names a region may not sit on this picture, or None if it may (or the label names no region).

    The folder is trusted: a picture from the region's folder may carry its name. It is blocked only when its main
    subject or first three subjects clearly belong somewhere else. A picture from another folder may not."""
    region = find_region(label, regions)
    if region is None:
        return None
    name = Path(rel).name
    if entry is None:
        return (f"the label {label!r} names {region['name']}, but {name} has no tags, so nothing says where it is: "
                "run tag_media.py, or use a picture from that region's folder")
    folder_region = find_region(entry.get("region") or "", regions)
    if folder_region is None:
        return (f"the label {label!r} names {region['name']}, but {name} is not from a region folder "
                f"(it is in {entry['folder']})")
    if folder_region["name"] != region["name"]:
        return f"the label {label!r} names {region['name']}, but {name} is from {folder_region['name']}"
    top = words_of(entry.get("main_subject", "")) + [w for s in entry.get("subjects", [])[:3] for w in words_of(s)]
    clash = [w for w in dict.fromkeys(top) if w in {stem(bad) for bad in region["not_here"]}]   # the word itself only
    if clash:
        return (f"the label {label!r} names {region['name']}, but {name} mainly shows {entry.get('main_subject')} "
                f"({', '.join(entry.get('subjects', [])[:3])}): {', '.join(clash)} doesn't fit that region")
    return None


# --- The used-media log ---

def load_used(path=USED_LOG):
    """The Shorts made so far, oldest first: [{"name", "made", "files"}]."""
    path = Path(path)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [s for s in data.get("shorts", []) if isinstance(s, dict) and isinstance(s.get("files"), list)]
    except (ValueError, AttributeError):
        raise PickError(f"{path} is not valid JSON; fix it or delete it to start the log again") from None


def save_used(name, files, path=USED_LOG):
    """Record a finished Short's pictures. Making the same Short again replaces its entry."""
    path = Path(path)
    shorts = [s for s in load_used(path) if s.get("name") != name]
    shorts.append({"name": name, "made": datetime.now().isoformat(timespec="seconds"), "files": sorted(set(files))})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"shorts": shorts}, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def recent_files(used, name=None, last=RECENT_SHORTS):
    """{file: how many Shorts ago it was last used (1 = the newest)} over the last few Shorts, leaving out the Short
    being made now."""
    others = [s for s in used if s.get("name") != name][-last:]
    seen = {}
    for ago, short in enumerate(reversed(others), 1):
        for rel in short["files"]:
            seen.setdefault(rel, ago)
    return seen


# --- Picking ---

def tag_words(entry):
    """A picture's tag words by where they are, each list holding stemmed words."""
    return {"main": words_of(entry.get("main_subject", "")),
            "names": [w for n in entry.get("names", []) for w in words_of(n)],
            "subjects": [w for s in entry.get("subjects", []) for w in words_of(s)],
            "region": words_of(entry.get("region") or ""),
            "setting": [w for s in entry.get("setting", []) for w in words_of(s)],
            "time": words_of(entry.get("time", "")),
            "type": words_of(entry.get("type", "")) + words_of(entry.get("shape", ""))}


def match(query, entry):
    """How well a picture's tags answer a query: {"score", "matched", "half", "missing"}. half are the words found
    only in the description."""
    words = query_words(query)
    fields = tag_words(entry)
    described = words_of(entry.get("description", ""))
    score, matched, half, missing = 0.0, [], [], []
    for word in words:
        best = max((WEIGHTS[name] for name, found in fields.items() if any(same_word(word, f) for f in found)),
                   default=0.0)
        if best:
            matched.append(word)
            score += best
        elif any(same_word(word, f) for f in described):
            half.append(word)
            score += DESCRIPTION_WEIGHT
        else:
            missing.append(word)
    return {"score": score, "matched": matched, "half": half, "missing": missing, "words": len(words)}


class Picker:
    """Picks pictures for one Short. tags is tag_media.load_tags(); recent is recent_files() of the Shorts before;
    seed (the Short's title) settles ties the same way every time."""

    def __init__(self, tags, recent=None, seed="", regions=None):
        self.tags = tags
        self.seed = seed
        self.recent = {}                         # group -> how many Shorts ago; a wallpaper's cuts are one picture
        for rel, ago in (recent or {}).items():
            group = self.group(rel)
            self.recent[group] = min(ago, self.recent.get(group, ago))
        self.regions = regions if regions is not None else load_regions()
        self.used_groups = {}                    # group -> the file that used it, in this Short
        self.picks = []

    def group(self, rel):
        return self.tags.get(rel, {}).get("group") or tag_media.group_of(rel)

    def reserve(self, rel):
        """A file the script names itself. Returns the file that already used this picture in this Short, if any."""
        group = self.group(rel)
        before = self.used_groups.get(group)
        self.used_groups.setdefault(group, rel)
        return before

    def candidates(self, region=None, text_ok=False, media="image", kind=None):
        for rel, entry in self.tags.items():
            if self.group(rel) in self.used_groups:
                continue
            overlay = entry.get("overlay", [])
            if "rating card" in overlay or (overlay and not text_ok):
                continue
            if media != "any" and entry["kind"] != media:
                continue
            if kind and entry.get("type") != kind:
                continue
            if region and (find_region(entry.get("region") or "", self.regions) or {}).get("name") != region["name"]:
                continue
            yield rel, entry

    def pick(self, query="", region=None, target="any", text_ok=False, media="image", kind=None, where=""):
        """The best picture for a shot, as a dict: file (relative to the media folder), ok, why, and what matched.
        ok is False when nothing matches well: file is then the closest match, to be named and not used."""
        wanted_region = None
        if region:
            wanted_region = find_region(region, self.regions)
            if wanted_region is None:
                raise PickError(f"{where}: region {region!r} is not one of "
                                f"{', '.join(r['name'] for r in self.regions)}")
        order = SHAPE_ORDER[target]
        ranked = []
        for rel, entry in self.candidates(wanted_region, text_ok, media, kind):
            m = match(query, entry)
            good = not m["missing"] and not m["half"]
            shape = SHAPE_BONUS[order.index(entry["shape"])] if entry.get("shape") in order else 0.0
            tie = hashlib.sha1(f"{self.seed}|{where}|{rel}".encode("utf-8")).hexdigest()
            # Best first: a full match, then more of the words, then not seen lately, then the stronger tags and shape.
            ranked.append((good, len(m["matched"]) + 0.5 * len(m["half"]), -(self.group(rel) in self.recent and good),
                           m["score"] + shape, tie, rel, m))
        if not ranked:
            limits = [f"region {wanted_region['name']}" if wanted_region else "", f"type {kind}" if kind else "",
                      "videos only" if media == "video" else "", "" if text_ok else "no text or logos on it"]
            return {"file": None, "ok": False, "query": query, "where": where, "matched": [], "missing": query_words(query),
                    "why": "no picture is left to pick from (" + ", ".join(x for x in limits if x)
                           + "; pictures already in this Short don't count)"}
        ranked.sort(reverse=True)
        good, _, _, score, _, rel, m = ranked[0]
        entry = self.tags[rel]
        result = {"file": rel, "ok": bool(good), "query": query, "where": where, "score": round(score, 2),
                  "matched": m["matched"], "missing": m["missing"] + m["half"], "region": entry.get("region"),
                  "shape": entry.get("shape"), "type": entry.get("type"), "notes": []}
        if not good:
            lost = (f"nothing shows {', '.join(m['missing'])}" if m["missing"] else
                    f"{', '.join(m['half'])} is only in its description, not its tags")
            result["why"] = (f"no good match for {query!r}"
                             + (f" in {wanted_region['name']}" if wanted_region else "")
                             + f": {lost}. The closest is {Path(rel).name} "
                             f"({entry.get('main_subject')}: {', '.join(entry.get('subjects', [])[:5])})")
            return result
        if self.group(rel) in self.recent:
            result["notes"].append(f"{Path(rel).name} was used {self.recent[self.group(rel)]} Short(s) ago: every good match for "
                                   f"{query!r} was used in the last {RECENT_SHORTS} Shorts")
        self.used_groups[self.group(rel)] = rel
        self.picks.append(result)
        return result


# --- Reading a script ---

def shot_target(shot, script_layout=None):
    """The shape a shot shows its picture in: full, square, wide or any."""
    beat = shot.get("beat")
    if beat in ("full", "banner", "lockon"):
        return "full"
    layout = shot.get("layout") or ("framed" if beat == "card" or "card_aspect" in shot else script_layout)
    if layout == "full":
        return "full"
    if layout == "framed":
        return {"16:9": "wide", "4:5": "full"}.get(shot.get("card_aspect"), "square")
    return "any"


def media_rel(path):
    """A script's file path as the tags know it ("rockstar/x.jpg" -> "x.jpg"), or None if it is not tagged media."""
    path = str(path).replace("\\", "/")
    return path[len(MEDIA_PREFIX):] if path.startswith(MEDIA_PREFIX) else None


def label_texts(shot):
    """The words a shot puts on its picture: its label, and a banner's text."""
    texts = []
    label = shot.get("label")
    if isinstance(label, str):
        texts.append(label)
    elif isinstance(label, dict) and isinstance(label.get("text"), str):
        texts.append(label["text"])
    if shot.get("beat") == "banner" and isinstance(shot.get("text"), str):
        texts.append(shot["text"])
    return texts


def resolve_script(script, tags, recent=None, regions=None, allow_weak=False):
    """A script with every "query" replaced by the file picked for it, ready for explainer.py.

    Returns (script, picks, problems, warnings). picks has one entry per picture shot, picked or named, in order:
    {"line", "shot", "file", "picked", "query", "say", "text", ...}. problems stop the Short; warnings don't.
    A weak match is a problem unless allow_weak, when the closest match is used and warned about."""
    regions = regions if regions is not None else load_regions()
    picker = Picker(tags, recent, str(script.get("title", "")), regions)
    problems, warnings, picks = [], [], []
    lines = script.get("lines") if isinstance(script.get("lines"), list) else []

    def shots_of(line):
        raw = line.get("beats", line.get("visuals")) if isinstance(line, dict) else None
        return raw if isinstance(raw, list) else []

    def pictures(shot):
        """The picture-holding parts of a shot: (holder dict, key, what it is called)."""
        if not isinstance(shot, dict):
            return []
        if shot.get("beat") == "compare":
            return [(shot[side], side) for side in ("top", "bottom") if isinstance(shot.get(side), dict)]
        return [(shot, "")]

    # First the files the script names itself, so a query never picks one of them again.
    for n, line in enumerate(lines, 1):
        for shot in shots_of(line):
            named = []
            if isinstance(shot, str) and not shot.startswith("card:"):
                named.append(shot.rpartition("@")[0] if "@" in shot else shot)
            for part, _ in pictures(shot):
                if isinstance(part.get("file"), str):
                    named.append(part["file"])
                if isinstance(part.get("bg"), str):
                    named.append(part["bg"].rpartition("@")[0] if "@" in part["bg"] else part["bg"])
            for path in named:
                rel = media_rel(path)
                if rel is None:
                    continue
                before = picker.reserve(rel)
                if before is not None:
                    warnings.append(f"line {n}: {Path(rel).name} shows a picture this Short already uses"
                                    + (f" ({Path(before).name})" if before != rel else ""))

    out = json.loads(json.dumps(script))
    for n, line in enumerate(out.get("lines") if isinstance(out.get("lines"), list) else [], 1):
        say = line.get("say", "") if isinstance(line, dict) else ""
        for i, shot in enumerate(shots_of(line), 1):
            where = f"line {n}, shot {i}"
            if isinstance(shot, str):
                if not shot.startswith("card:"):
                    path = shot.rpartition("@")[0] if "@" in shot else shot
                    picks.append({"line": n, "shot": i, "side": "", "file": media_rel(path), "path": path,
                                  "picked": False, "query": None, "weak": False, "say": say, "text": [], "beat": None})
                continue
            if not isinstance(shot, dict):
                continue
            for part, side in pictures(shot):
                name = f"{where}{' (' + side + ')' if side else ''}"
                texts = label_texts(part)
                asked = "query" in part or ("region" in part and "file" not in part)
                if "file" in part and any(k in part for k in QUERY_KEYS):
                    problems.append(f"{name}: a shot has a file or a query, one of the two")
                    continue
                if asked and shot.get("beat") == "lockon":
                    problems.append(f"{name}: a lock-on's box is in one picture's own pixels, so it needs a file, "
                                    "not a query")
                    continue
                if asked:
                    media = part.get("media", "image")
                    if media not in MEDIA_KINDS:
                        problems.append(f"{name}: media is image, video or any")
                        continue
                    label_region = next((r for r in (find_region(t, regions) for t in texts) if r), None)
                    region = part.get("region") or (label_region["name"] if label_region else None)
                    try:
                        got = picker.pick(str(part.get("query", "")), region,
                                          "wide" if side else shot_target(part, script.get("layout")),
                                          part.get("text_ok") is True, media, part.get("type"), name)
                    except PickError as e:
                        problems.append(str(e))
                        continue
                    if not got["ok"]:
                        if got["file"] is None or not allow_weak:
                            problems.append(f"{name}: {got['why']}")
                            continue
                        warnings.append(f"{name}: WEAK MATCH used. {got['why']}")
                        picker.used_groups[picker.group(got["file"])] = got["file"]
                    warnings += [f"{name}: {note}" for note in got.get("notes", [])]
                    rel, entry = got["file"], tags[got["file"]]
                    for key in QUERY_KEYS:
                        part.pop(key, None)
                    part["file"] = MEDIA_PREFIX + rel
                    if entry["kind"] == "video":
                        if entry.get("start_s") and "at" not in part:
                            part["at"] = str(entry["start_s"])
                    if "focus" not in part and "pan" not in part and entry.get("focus"):
                        part["focus"] = entry["focus"]
                    picked = True
                else:
                    rel, picked, got = media_rel(part.get("file", "")), False, {}
                    entry = tags.get(rel) if rel else None
                if "file" in part:
                    for text in texts:
                        wrong = region_label_problem(text, rel or part["file"], entry, regions)
                        if wrong:
                            problems.append(f"{name}: {wrong}")
                    picks.append({"line": n, "shot": i, "side": side, "file": rel, "path": part["file"],
                                  "picked": picked, "query": got.get("query"), "weak": picked and not got.get("ok"),
                                  "say": say, "text": texts, "beat": shot.get("beat"),
                                  "box": part.get("box"), "focus": part.get("focus")})
                # The picture behind a text card or a snippet may be asked for too.
                bg = part.get("bg")
                if isinstance(bg, dict):
                    try:
                        got = picker.pick(str(bg.get("query", "")), bg.get("region"), "full",
                                          bg.get("text_ok") is True, bg.get("media", "image"), bg.get("type"),
                                          name + " (behind)")
                    except PickError as e:
                        problems.append(str(e))
                        continue
                    if not got["ok"]:
                        if got["file"] is None or not allow_weak:
                            problems.append(f"{name} (behind): {got['why']}")
                            part.pop("bg")
                            continue
                        warnings.append(f"{name} (behind): WEAK MATCH used. {got['why']}")
                        picker.used_groups[picker.group(got["file"])] = got["file"]
                    part["bg"] = MEDIA_PREFIX + got["file"]
                    picks.append({"line": n, "shot": i, "side": "behind", "file": got["file"], "path": part["bg"],
                                  "picked": True, "query": got.get("query"), "weak": not got["ok"], "say": say,
                                  "text": [part.get("card") or part.get("text") or ""], "beat": shot.get("beat")})
    return out, picks, problems, warnings


def main():
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Show which picture each shot of a script would get.")
    parser.add_argument("script", type=Path)
    parser.add_argument("--allow-weak", action="store_true", help="use the closest match when nothing matches well")
    args = parser.parse_args()
    try:
        script = json.loads(args.script.read_bytes().decode("utf-8-sig"))
        tags = tag_media.load_tags()
        _, picks, problems, warnings = resolve_script(script, tags, recent_files(load_used()),
                                                      allow_weak=args.allow_weak)
    except (OSError, ValueError, PickError, tag_media.TagError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    for p in picks:
        how = f"picked for {p['query']!r}" if p["picked"] else "named in the script"
        print(f"line {p['line']}, shot {p['shot']}{' ' + p['side'] if p.get('side') else ''}: {p['file'] or p.get('path')}  ({how})")
    for warning in warnings:
        print(f"WARNING: {warning}")
    for problem in problems:
        print(f"PROBLEM: {problem}")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
