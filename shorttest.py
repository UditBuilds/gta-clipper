"""Checks for the topic-to-Short tools (PR 19): the picture tagger (tag_media.py) on made-up model answers, the
picture picker (picker.py) on made-up tags, the facts file and its validator (facts.py), and make_short.py with a
stand-in for the render.

No Ollama, no Rockstar files and no Kokoro are used. ffmpeg makes a few tiny pictures and short videos in a temporary
folder, and stand-ins answer for the vision model and for explainer.py's render.

Each check prints PASS or FAIL with what it tests. A check that raises counts as FAIL (with the error), so a missing
function shows as a failure, not a crash. The last line is RESULT: PASS or FAIL; exit code 0 or 1.

Usage:  python shorttest.py
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import facts
import make_short
import picker
import tag_media

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def raises(error, fn, *args, **kwargs):
    """The error's text if fn raises it, else None."""
    try:
        fn(*args, **kwargs)
    except error as e:
        return str(e) or type(e).__name__
    return None


# --- The tagger ---

ANSWER = {"description": "An airboat crosses a swamp while an alligator watches from the reeds.",
          "main_subject": "Airboat", "subjects": ["Airboat", "alligator", "swamp", "airboat", " reeds. "],
          "setting": ["swamp", "the moon"], "time": "day", "people": ["man"], "focus_x": 325, "focus_y": 600}
PLACE = "GTAVI_Screenshots/Places/Grassrivers/Grassrivers_01.jpg"
PERSON = "GTAVI_Screenshots/People/Jason and Lucia/Jason_and_Lucia_01.jpg"
VIDEO = "GTAVI_Videos/Lucia_Caminos_Video_Clip.mp4"


class FakeOllama:
    """Stands in for vlm's calls to Ollama."""
    unloaded = 0

    def check_ollama(self, model):
        return {"digest": "x", "capabilities": ["vision"], "loaded": []}

    def model_memory(self, model):
        return None

    def unload(self, model):
        self.unloaded += 1


def make_media(root):
    """Two 64x36 pictures and a 1 s video where the tagger looks for them."""
    for rel, colour in ((PLACE, "green"), (PERSON, "red")):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color={colour}:s=64x36", "-frames:v", "1",
                        str(root / rel)], check=True)
    (root / VIDEO).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=blue:s=64x64:r=10", "-t", "1",
                    "-pix_fmt", "yuv420p", str(root / VIDEO)], check=True)


NO_TEXT = {"words_seen": [], "big_text": False, "logo": False, "rating_card": False}


def tag_run(root, calls, answers=None, text=NO_TEXT, text_calls=None, **flags):
    """One run of the tagger on root with a stand-in model. calls collects the frames it was asked to tag, and
    text_calls the frames it was asked about text and logos on. text is its answer to every one of those."""
    def ask(model, jpeg, capabilities, what="tags"):
        if what == "text":
            if text_calls is not None:
                text_calls.append(Path(jpeg).name)
            return tag_media.parse_text(json.dumps(text)), 0.1
        calls.append(Path(jpeg).name)
        answer = (answers or {}).get(len(calls), ANSWER)
        return tag_media.parse_answer(json.dumps(answer)), 0.1
    args = argparse.Namespace(**{"root": root, "model": "fake-model", "force": False, "only": None, "sheet": False,
                                 "out": None, "long_side": 64, **flags})
    lines = []
    code = tag_media.run(args, log=lines.append, ask=ask, ollama=FakeOllama())
    return code, lines


@check
def tagger_cleans_the_models_answer():
    """the model's answer is tidied: tags lowercase and without repeats, a setting that isn't on the list dropped,
    and its 0-1000 focus becomes 0-1"""
    tags = tag_media.parse_answer(json.dumps(ANSWER))
    return (tags["subjects"] == ["airboat", "alligator", "swamp", "reeds"] and tags["main_subject"] == "airboat"
            and tags["setting"] == ["swamp"] and tags["people"] == ["man"] and tags["focus"] == [0.33, 0.6]
            and tags["time"] == "day")


@check
def tagger_refuses_an_answer_it_cant_use():
    """an answer that isn't JSON, has no subjects, or has no focus number is refused, so the file stays untagged
    instead of getting made-up tags"""
    return all(raises(ValueError, tag_media.parse_answer, text) for text in
               ("not json", json.dumps({**ANSWER, "subjects": []}), json.dumps({**ANSWER, "focus_x": "left"}),
                json.dumps([1, 2])))


@check
def region_and_names_come_from_the_folder():
    """the region is the Places folder; a People folder (or a video's file name) gives the character; a name is kept
    only when the model saw that kind of person (no woman seen: no Lucia)"""
    facts = tag_media.folder_facts
    return (facts(PLACE) == {"region": "Grassrivers", "character": None}
            and facts(PERSON) == {"region": None, "character": "jason and lucia"}
            and facts(VIDEO) == {"region": None, "character": "lucia caminos"}
            and facts("GTAVI_Videos/GTAVI_Official_Cover_Art_Landscape.mp4") == {"region": None, "character": None}
            and tag_media.names_for("jason and lucia", ["man"]) == ["Jason"]
            and tag_media.names_for("jason and lucia", ["man", "woman"]) == ["Jason", "Lucia"]
            and tag_media.names_for("lucia caminos", ["man"]) == [] and tag_media.names_for(None, ["man"]) == [])


@check
def type_shape_and_group_come_from_the_file():
    """a file's type is its top folder (cover art is key art wherever it is), its shape is its size (2160x4800 is
    phone, 2160x3840 portrait, 3840x2160 landscape), and the six cuts of one wallpaper share a group, with or
    without logos, while every screenshot is its own"""
    wall = "GTAVI_Artwork_Wallpapers/"
    kind, group = tag_media.media_type, tag_media.group_of
    return (kind(PLACE) == "screenshot" and kind(VIDEO) == "video loop"
            and kind(wall + "Boobie_Ike/Boobie_Ike_phone.jpg") == "wallpaper"
            and kind(wall + "Official_Cover_Art/Official_Cover_Art_square.jpg") == "key art"
            and kind("GTAVI_Videos/GTAVI_Official_Cover_Art_Landscape.mp4") == "key art"
            and kind("GTAVI_Ultimate_Edition_Benefits/ULTIMATE_EDITION_01.jpg") == "edition"
            and kind("GTAVI_Vintage_Vice_City_Pack/VINTAGE_VICE_CITY_PACK_01.jpg") == "edition"
            and [tag_media.shape_of(*s) for s in ((2160, 4800), (2160, 3840), (2160, 2160), (3840, 3840), (3840, 2160),
                                                  (5120, 2160))]
            == ["phone", "portrait", "square", "square", "landscape", "ultrawide"]
            and group(wall + "Jason_and_Lucia_01/Jason_and_Lucia_01_phone.jpg")
            == group(wall + "Jason_and_Lucia_01_With_Logos/Jason_and_Lucia_01_With_Logos_square.jpg")
            == wall + "Jason_and_Lucia_01"
            and group(PLACE) == PLACE
            and tag_media.folder_facts(wall + "Postcards/Mount_Kalaga_National_Park/x_phone.jpg")["region"]
            == "Mount Kalaga National Park"
            and tag_media.folder_facts(wall + "Jason_Lucia_Motel/Jason_and_Lucia_Motel_phone.jpg")["character"]
            == "jason lucia")


@check
def text_and_logos_are_saved_for_every_file():
    """every file is also asked what is printed on it: the answer (big text, logo, rating card) is saved with the
    words read, and a picture from a "With_Logos" folder is marked logo even when the model saw none"""
    with tempfile.TemporaryDirectory() as tmp:
        root, calls = Path(tmp), []
        make_media(root)
        logos = "GTAVI_Artwork_Wallpapers/Jason_and_Lucia_01_With_Logos/Jason_and_Lucia_01_With_Logos_square.jpg"
        (root / logos).parent.mkdir(parents=True)
        (root / logos).write_bytes((root / PLACE).read_bytes())
        tag_run(root, calls, only=["Jason_and_Lucia"])
        tag_run(root, calls, text={"words_seen": ["GRASSRIVERS", "Visit Leonida"], "big_text": True, "logo": False,
                                   "rating_card": True})
        tags = tag_media.load_tags(root)
        return (tags[PLACE]["overlay"] == ["big text", "rating card"]
                and tags[PLACE]["overlay_seen"] == ["grassrivers", "visit leonida"]
                and tags[PERSON]["overlay"] == [] and tags[logos]["overlay"] == ["logo"]
                and tags[logos]["type"] == "wallpaper" and tags[PLACE]["type"] == "screenshot"
                and tags[PLACE]["shape"] == "landscape" and tags[VIDEO]["shape"] == "square"
                and tags[VIDEO]["overlay"] == ["big text", "rating card"] and tags[VIDEO]["start_s"] == 0.0)


@check
def tags_made_before_the_text_check_are_kept():
    """a file tagged before the text check existed is asked only the new question: its tags, which the owner has
    already checked, stay exactly as they were"""
    with tempfile.TemporaryDirectory() as tmp:
        root, calls, text_calls = Path(tmp), [], []
        make_media(root)
        tag_run(root, calls)
        path = root / "_tags.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for entry in data["files"].values():        # as Step 1 left them
            for key in ("type", "shape", "group", "overlay", "overlay_seen", "text_version"):
                entry.pop(key)
            entry["subjects"] = ["checked", "by", "the owner"]
        path.write_text(json.dumps(data), encoding="utf-8")
        before = len(calls)
        _, lines = tag_run(root, calls, text={**NO_TEXT, "logo": True}, text_calls=text_calls)
        files = json.loads(path.read_text(encoding="utf-8"))["files"]
        return (len(calls) == before and len(text_calls) == 4
                and all(f["subjects"] == ["checked", "by", "the owner"] and f["overlay"] == ["logo"]
                        and f["type"] and f["shape"] for f in files.values())
                and any("3 of them only need the check for text and logos" in line for line in lines))


@check
def a_long_video_starts_after_its_rating_card():
    """a video longer than 4 s is searched for where its own picture starts: frames with a rating card are passed
    over, black frames are passed over without asking the model, and the first frame after them is the start"""
    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "cover.mp4"
        parts = "color=white:s=64x36:r=10:d=2[a];color=black:s=64x36:r=10:d=1.5[b];color=red:s=64x36:r=10:d=3[c];[a][b][c]concat=n=3"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", parts, "-pix_fmt", "yuv420p", str(video)],
                       check=True)
        asked = []

        def look(at):
            asked.append(at)
            return {"overlay": ["rating card"] if at < 2 else [], "seen": []}
        start = tag_media.picture_start(video, 6.5, look)
        never = []
        short = tag_media.picture_start(video, 1.5, never.append)
        return start == 3.5 and asked == [0.0, 0.5, 1.0, 1.5, 3.5] and short == 0.0 and not never


@check
def tagger_tags_pictures_and_two_frames_of_a_video():
    """a run tags both pictures (one frame each) and the video (its first and middle frame), saves region, names,
    focus and the model's name for each, and unloads the model"""
    with tempfile.TemporaryDirectory() as tmp:
        root, calls = Path(tmp), []
        make_media(root)
        code, _ = tag_run(root, calls)
        files = json.loads((root / "_tags.json").read_text(encoding="utf-8"))["files"]
        video = files[VIDEO]
        return (code == 0 and len(calls) == 4 and set(files) == {PLACE, PERSON, VIDEO}
                and files[PLACE]["region"] == "Grassrivers" and files[PLACE]["names"] == []
                and files[PERSON]["names"] == ["Jason"] and files[PERSON]["region"] is None
                and files[PLACE]["focus"] == [0.33, 0.6] and files[PLACE]["model"] == "fake-model"
                and files[PLACE]["size"] == [64, 36] and video["kind"] == "video"
                and [f["at_s"] for f in video["frames"]] == [0.0, 0.5] and video["names"] == [])


@check
def tagger_skips_what_is_tagged_unless_forced():
    """a second run asks the model nothing; --force asks about every frame again; a file that was replaced by
    another (its size changed) is tagged again without --force"""
    with tempfile.TemporaryDirectory() as tmp:
        root, calls = Path(tmp), []
        make_media(root)
        tag_run(root, calls)
        first = len(calls)
        _, lines = tag_run(root, calls)
        second = len(calls) - first
        tag_run(root, calls, force=True)
        forced = len(calls) - first - second
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=64x36", "-frames:v", "1",
                        str(root / PLACE)], check=True)
        before = len(calls)
        tag_run(root, calls)
        return (first == 4 and second == 0 and forced == 4 and len(calls) - before == 1
                and any("3 already tagged, 0 to tag" in line for line in lines))


@check
def override_file_wins_over_the_model():
    """a fix in _tags_override.json replaces the model's tag when the tags are read, the fields it leaves out keep the
    model's value, and it still wins after a --force re-tag; the tagger never writes the override file"""
    with tempfile.TemporaryDirectory() as tmp:
        root, calls = Path(tmp), []
        make_media(root)
        tag_run(root, calls)
        fix = {"files": {PLACE: {"subjects": ["Helicopter", "forest"], "focus": [0.5, 0.25], "region": None}}}
        text = json.dumps(fix)
        (root / "_tags_override.json").write_text(text, encoding="utf-8")
        tag_run(root, calls, force=True)
        tags = tag_media.load_tags(root)
        raw = json.loads((root / "_tags.json").read_text(encoding="utf-8"))["files"][PLACE]
        return (tags[PLACE]["subjects"] == ["helicopter", "forest"] and tags[PLACE]["focus"] == [0.5, 0.25]
                and tags[PLACE]["region"] is None and tags[PLACE]["setting"] == ["swamp"]
                and tags[PLACE]["overridden"] == ["focus", "region", "subjects"]
                and raw["subjects"] == ["airboat", "alligator", "swamp", "reeds"]
                and "overridden" not in tags[PERSON]
                and (root / "_tags_override.json").read_text(encoding="utf-8") == text)


@check
def a_broken_override_file_stops_with_a_reason():
    """an override with a tag name that doesn't exist, a focus outside 0-1, or text that isn't JSON stops with an
    error that names the file, instead of being ignored"""
    with tempfile.TemporaryDirectory() as tmp:
        root, calls = Path(tmp), []
        make_media(root)
        tag_run(root, calls)
        problems = []
        for bad in ('{"files": {"%s": {"subject": ["x"]}}}' % PLACE, '{"files": {"%s": {"focus": [2, 0.5]}}}' % PLACE,
                    "{not json"):
            (root / "_tags_override.json").write_text(bad, encoding="utf-8")
            problems.append(raises(tag_media.TagError, tag_media.load_tags, root))
        return (all(problems) and "'subject' is not a tag" in problems[0] and "focus is [across, down]" in problems[1]
                and "_tags_override.json" in problems[2])


@check
def an_unusable_answer_leaves_the_file_untagged():
    """when the model's answer for one picture can't be used, that picture is reported and left untagged (exit code 1),
    the others are saved, and the next run tries only that one again"""
    with tempfile.TemporaryDirectory() as tmp:
        root, calls = Path(tmp), []
        make_media(root)
        code, lines = tag_run(root, calls, answers={1: {**ANSWER, "subjects": []}})
        files = json.loads((root / "_tags.json").read_text(encoding="utf-8"))["files"]
        again = []
        code2, _ = tag_run(root, again)
        return (code == 1 and len(files) == 2 and any("NOT TAGGED" in line for line in lines)
                and code2 == 0 and len(again) == 1 and len(tag_media.load_tags(root)) == 3)


@check
def check_sheet_has_every_file_and_its_thumbnails():
    """the check sheet is written to _tag_check\\index.html with one thumbnail per picture and two per video, holds
    the model's tags and the owner's fixes separately, and says where the override file goes"""
    with tempfile.TemporaryDirectory() as tmp:
        root, calls = Path(tmp), []
        make_media(root)
        (root / "_tags_override.json").write_text(json.dumps({"files": {PLACE: {"main_subject": "helicopter"}}}),
                                                  encoding="utf-8")
        _, lines = tag_run(root, calls)
        page = (root / "_tag_check" / "index.html").read_text(encoding="utf-8")
        data = json.loads(page.split("const DATA = ", 1)[1].split(";\n", 1)[0])
        thumbs = sorted(p.name for p in (root / "_tag_check" / "thumbs").iterdir())
        by_path = {item["path"]: item for item in data["items"]}
        return (len(data["items"]) == 3 and len(thumbs) == 4 and len(by_path[VIDEO]["thumbs"]) == 2
                and all((root / "_tag_check" / t).is_file() for item in data["items"] for t in item["thumbs"])
                and by_path[PLACE]["model"]["main_subject"] == "airboat"
                and data["override"] == {PLACE: {"main_subject": "helicopter"}}
                and data["media_root"] == str(root) and "/*DATA*/" not in page
                and any("Check sheet:" in line for line in lines))


# --- The picker ---

SHOTS, WALLS = "GTAVI_Screenshots/Places/", "GTAVI_Artwork_Wallpapers/"
AIRBOAT, GATOR = SHOTS + "Grassrivers/Grassrivers_01.jpg", SHOTS + "Grassrivers/Grassrivers_02.jpg"
HELI, DEER = SHOTS + "Mount Kalaga National Park/Kalaga_01.jpg", SHOTS + "Mount Kalaga National Park/Kalaga_02.jpg"
CITY_IN_KALAGA = SHOTS + "Mount Kalaga National Park/Kalaga_03.jpg"
SKYLINE = SHOTS + "Vice City/Vice_City_01.jpg"
JASON = "GTAVI_Screenshots/People/Jason Duval/Jason_Duval_01.jpg"
JL = {shape: f"{WALLS}Jason_and_Lucia_01/Jason_and_Lucia_01_{shape}.jpg" for shape in ("phone", "landscape", "square")}
JL_LOGO = WALLS + "Jason_and_Lucia_01_With_Logos/Jason_and_Lucia_01_With_Logos_landscape.jpg"
EDITION = "GTAVI_Ultimate_Edition_Benefits/ULTIMATE_EDITION_01.jpg"
RATED = "GTAVI_Ultimate_Edition_Benefits/ULTIMATE_EDITION_02.jpg"
COVER, LOOP = "GTAVI_Videos/GTAVI_Official_Cover_Art_Landscape.mp4", "GTAVI_Videos/Jason_Duval_Video_Clip.mp4"


def tagged(rel, main, subjects, setting=(), overlay=(), names=(), shape="landscape", **more):
    """One made-up entry of the tags file, with what the tagger would save for that path."""
    facts = tag_media.folder_facts(rel)
    return {"kind": "video" if rel.endswith(".mp4") else "image", "folder": str(Path(rel).parent.as_posix()),
            "region": facts["region"], "description": f"A picture of {main}.", "main_subject": main,
            "subjects": list(subjects), "setting": list(setting), "time": "day", "people": [], "names": list(names),
            "focus": [0.3, 0.6], "type": tag_media.media_type(rel), "shape": shape, "group": tag_media.group_of(rel),
            "overlay": list(overlay), **more}


def fake_tags():
    tags = {AIRBOAT: tagged(AIRBOAT, "airboat", ["airboat", "man", "reeds"], ["swamp"]),
            GATOR: tagged(GATOR, "alligator", ["alligator", "water", "trees"], ["swamp"]),
            HELI: tagged(HELI, "helicopter", ["helicopter", "mountain", "clouds"], ["mountain", "forest"]),
            DEER: tagged(DEER, "deer", ["deer", "trees", "water"], ["forest"]),
            CITY_IN_KALAGA: tagged(CITY_IN_KALAGA, "city skyline", ["skyscrapers", "bridge", "cars"], ["city"]),
            SKYLINE: tagged(SKYLINE, "city skyline", ["skyscrapers", "bridge", "river"], ["city"], time="night"),
            JASON: tagged(JASON, "man with gun", ["man", "gun", "fence"], ["city"], names=["Jason"]),
            JL_LOGO: tagged(JL_LOGO, "man and woman", ["man", "woman", "car"], ["city"], ["logo"], ["Jason", "Lucia"]),
            EDITION: tagged(EDITION, "sports car", ["car", "garage"], ["interior"], ["big text"]),
            RATED: tagged(RATED, "sports car", ["car", "label"], ["interior"], ["rating card"]),
            COVER: tagged(COVER, "game logo", ["logo", "man", "woman"], [], ["logo"], start_s=7.0, length_s=32.7),
            LOOP: tagged(LOOP, "man with bandana", ["man", "bandana", "store"], ["interior"], names=["Jason"],
                         start_s=0.0, length_s=1.0)}
    for shape, rel in JL.items():
        tags[rel] = tagged(rel, "man and woman", ["man", "woman", "car"], ["city"], names=["Jason", "Lucia"], shape=shape)
    return tags


def shot_script(*shots, **top):
    return {"title": "A test Short", "lines": [{"say": "One line.", "beats": list(shots)}], **top}


def resolved(*shots, allow_weak=False, recent=None, **top):
    """(the shots as explainer.py would get them, picks, problems, warnings)."""
    out, picks, problems, warnings = picker.resolve_script(shot_script(*shots, **top), fake_tags(), recent,
                                                          allow_weak=allow_weak)
    return out["lines"][0]["beats"], picks, problems, warnings


@check
def picker_finds_a_picture_by_what_it_shows():
    """a query finds the picture whose tags hold its words: "swamp airboat" in Grassrivers is the airboat, "gators"
    (a plural and a nickname) is the alligator, and "boat" finds the airboat because it ends in boat, while "phone"
    does not find "headphones\""""
    p = picker.Picker(fake_tags(), seed="x")
    first = p.pick("swamp airboat", "Grassrivers")
    second = p.pick("gators")
    third = picker.Picker(fake_tags(), seed="x").pick("a boat in the swamp")
    dj = {JASON: tagged(JASON, "woman with headphones", ["woman", "headphones", "speaker"])}
    return (first["file"] == AIRBOAT and first["ok"] and second["file"] == GATOR and second["ok"]
            and third["file"] == AIRBOAT and third["matched"] == ["boat", "swamp"]
            and picker.Picker(dj).pick("woman phone")["ok"] is False        # "phone" is not "headphones"
            and picker.Picker(dj).pick("woman headphone")["ok"] is True)


@check
def a_query_becomes_a_file_explainer_can_render():
    """in a script a shot with "query" comes back with the picked "file" under rockstar/, the tags' focus point, and
    none of the picker's own settings, while a shot that names a file is left exactly as it was"""
    named = {"beat": "card", "file": "rockstar/" + DEER, "label": "Look here"}
    shots, picks, problems, warnings = resolved({"beat": "full", "query": "swamp airboat", "region": "Grassrivers"},
                                                dict(named))
    return (not problems and not warnings
            and shots[0] == {"beat": "full", "file": "rockstar/" + AIRBOAT, "focus": [0.3, 0.6]}
            and shots[1] == named and [p["picked"] for p in picks] == [True, False]
            and picks[0]["query"] == "swamp airboat" and picks[1]["file"] == DEER)


@check
def picker_never_repeats_a_picture_in_one_short():
    """two shots that ask for the same thing get two different pictures; a third finds none left and is refused; a
    picture the script names by path is never picked again by a query; and the cuts of one wallpaper are one picture"""
    shots, _, problems, _ = resolved({"beat": "full", "query": "swamp"}, {"beat": "full", "query": "swamp"},
                                     {"beat": "full", "query": "swamp"})
    files = [s.get("file") for s in shots]
    named, _, named_problems, _ = resolved({"beat": "card", "file": "rockstar/" + AIRBOAT},
                                           {"beat": "full", "query": "airboat"})
    walls, _, wall_problems, _ = resolved({"beat": "full", "query": "Jason Lucia"}, {"beat": "card", "query": "Jason Lucia"})
    return (set(files[:2]) == {"rockstar/" + AIRBOAT, "rockstar/" + GATOR} and "file" not in shots[2]
            and len(problems) == 1 and "line 1, shot 3" in problems[0]
            and len(named_problems) == 1 and "airboat" in named_problems[0] and "file" not in named[1]
            and walls[0]["file"] == "rockstar/" + JL["phone"] and len(wall_problems) == 1 and "file" not in walls[1])


@check
def a_poor_match_is_refused_and_names_the_closest():
    """when nothing shows what the query asks for, the Short stops with a problem that says what is missing and names
    the closest picture; with allow_weak that picture is used, and a WEAK MATCH warning says so; never silently"""
    shots, _, problems, warnings = resolved({"beat": "full", "query": "airboat spaceship"})
    used, picks, no_problems, warned = resolved({"beat": "full", "query": "airboat spaceship"}, allow_weak=True)
    half = picker.Picker(fake_tags()).pick("picture deer")    # "picture" is only in the description
    return ("file" not in shots[0] and not warnings and len(problems) == 1
            and "nothing shows spaceship" in problems[0] and "Grassrivers_01.jpg" in problems[0]
            and used[0]["file"] == "rockstar/" + AIRBOAT and not no_problems
            and len(warned) == 1 and "WEAK MATCH" in warned[0] and picks[0]["weak"] is True
            and half["ok"] is False and "only in its description" in half["why"])


@check
def pictures_of_the_last_five_shorts_are_avoided():
    """a picture used in one of the last 5 Shorts is passed over while another good match exists; when every good
    match was used lately, one is still picked and a warning says how long ago it was used"""
    fresh, _, _, quiet = resolved({"beat": "full", "query": "swamp"}, recent={AIRBOAT: 1})
    both, _, problems, noted = resolved({"beat": "full", "query": "swamp"}, recent={AIRBOAT: 2, GATOR: 4})
    cut, _, _, cut_notes = resolved({"beat": "full", "query": "Jason Lucia"}, recent={JL["square"]: 3})
    return (fresh[0]["file"] == "rockstar/" + GATOR and not quiet
            and both[0]["file"] in ("rockstar/" + AIRBOAT, "rockstar/" + GATOR) and not problems
            and len(noted) == 1 and "Short(s) ago" in noted[0]
            and cut[0]["file"] == "rockstar/" + JL["phone"] and "3 Short(s) ago" in cut_notes[0])


@check
def used_media_log_keeps_each_short_once():
    """the used-media log records each Short's pictures; making a Short again replaces its entry; only the last 5
    other Shorts count as recent, and the Short being made now is left out"""
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "shorts" / "_used_media.json"
        empty = picker.load_used(log)
        for n in range(1, 8):
            picker.save_used(f"short-{n}", [f"pic_{n}.jpg", "always.jpg"], log)
        picker.save_used("short-7", ["pic_7b.jpg"], log)
        used = picker.load_used(log)
        recent = picker.recent_files(used)
        mine = picker.recent_files(used, name="short-7")
        return (empty == [] and [s["name"] for s in used] == [f"short-{n}" for n in range(1, 8)]
                and used[-1]["files"] == ["pic_7b.jpg"]
                and recent == {"pic_7b.jpg": 1, "pic_6.jpg": 2, "always.jpg": 2, "pic_5.jpg": 3, "pic_4.jpg": 4,
                               "pic_3.jpg": 5}
                and "pic_7b.jpg" not in mine and mine["pic_6.jpg"] == 1 and mine["pic_2.jpg"] == 5
                and "pic_1.jpg" not in mine)


@check
def text_logos_and_rating_cards_stay_out():
    """a picture with big text or a logo is never picked unless the shot says "text_ok": true; with it the edition
    picture can be picked; a picture with an age-rating card is never picked, even then; and an edition picture,
    even a clean one, is picked only by a shot that says "type": "edition\""""
    plain, _, problems, _ = resolved({"beat": "card", "query": "sports car", "type": "edition"})
    asked, _, asked_problems, _ = resolved({"beat": "card", "query": "sports car", "type": "edition", "text_ok": True},
                                           {"beat": "card", "query": "sports car", "type": "edition", "text_ok": True})
    tags = fake_tags()
    clean = "GTAVI_Ultimate_Edition_Benefits/ULTIMATE_EDITION_03.jpg"
    tags[clean] = tagged(clean, "sports car", ["car", "road"])
    general = picker.Picker(tags).pick("sports car")
    edition = picker.Picker(tags).pick("sports car", kind="edition")
    if general["ok"] or edition["file"] != clean:
        return False
    logo, _, _, _ = resolved({"beat": "compare", "top": {"query": "Jason Lucia", "label": "Then"},
                              "bottom": {"file": "rockstar/" + DEER, "label": "Now"}})
    return ("file" not in plain[0] and len(problems) == 1
            and asked[0]["file"] == "rockstar/" + EDITION and "text_ok" not in asked[0]
            and "file" not in asked[1] and len(asked_problems) == 1
            and logo[0]["top"]["file"] == "rockstar/" + JL["landscape"])


@check
def a_full_screen_shot_prefers_a_phone_shaped_picture():
    """among equal matches a full-screen shot takes the phone-shaped cut, a square card the square cut and a
    compare's 16:9 card the landscape cut; shape never beats a better match"""
    full, _, _, _ = resolved({"beat": "full", "query": "Jason Lucia"})
    banner, _, _, _ = resolved({"beat": "banner", "text": "The catch", "query": "Jason Lucia"})
    card, _, _, _ = resolved({"beat": "card", "query": "Jason Lucia"})
    wide, _, _, _ = resolved({"beat": "compare", "top": {"query": "Jason Lucia"}, "bottom": {"file": "rockstar/" + DEER}})
    better, _, _, _ = resolved({"beat": "full", "query": "Jason gun"})
    return (full[0]["file"] == banner[0]["file"] == "rockstar/" + JL["phone"]
            and card[0]["file"] == "rockstar/" + JL["square"] and wide[0]["top"]["file"] == "rockstar/" + JL["landscape"]
            and better[0]["file"] == "rockstar/" + JASON)


@check
def a_video_is_picked_only_when_asked_and_starts_after_its_rating_card():
    """a query picks pictures only, unless the shot says "media": "video"; the cover art video then starts at the
    second its own picture begins (after the age-rating card), and a short loop starts at 0 with no "at\""""
    still, _, _, _ = resolved({"beat": "full", "query": "man bandana store"}, allow_weak=True)
    loop, _, problems, _ = resolved({"beat": "full", "query": "man bandana store", "media": "video"})
    cover, _, cover_problems, _ = resolved({"beat": "full", "query": "game logo", "media": "video", "text_ok": True})
    bad, _, bad_problems, _ = resolved({"beat": "full", "query": "man", "media": "film"})
    return (not still[0]["file"].endswith(".mp4")
            and loop[0] == {"beat": "full", "file": "rockstar/" + LOOP, "focus": [0.3, 0.6]} and not problems
            and cover[0]["file"] == "rockstar/" + COVER and cover[0]["at"] == "7.0" and not cover_problems
            and len(bad_problems) == 1 and "media is image, video or any" in bad_problems[0])


@check
def a_region_label_needs_a_picture_from_that_region():
    """a label that names a region is allowed on a picture from that region's folder (the folder is trusted, so the
    helicopter over the mountain passes), and refused on a picture from another region, on one from no region folder,
    and on one whose subjects clearly contradict it (a city skyline in the Mount Kalaga folder); other labels are free"""
    def problems_for(rel, label, beat="card"):
        shot = {"beat": beat, "file": "rockstar/" + rel, "label": label}
        if beat == "banner":
            shot = {"beat": beat, "file": "rockstar/" + rel, "text": label}
        return resolved(shot)[2]
    wrong = problems_for(SKYLINE, "MOUNT KALAGA")
    city = problems_for(CITY_IN_KALAGA, {"text": "Mount Kalaga | hunting", "style": "info"})
    person = problems_for(JASON, "Vice City", beat="banner")
    untagged = problems_for("GTAVI_Screenshots/Places/Vice City/Not_Tagged.jpg", "Vice City")
    return (problems_for(HELI, "MOUNT KALAGA") == [] and problems_for(DEER, "Mount Kalaga National Park") == []
            and problems_for(SKYLINE, "Vice City") == [] and problems_for(SKYLINE, "Look here") == []
            and len(wrong) == 1 and "is from Vice City" in wrong[0]
            and len(city) == 1 and "mainly shows city skyline" in city[0]
            and len(person) == 1 and "not from a region folder" in person[0]
            and len(untagged) == 1 and "has no tags" in untagged[0])


@check
def a_region_label_steers_the_pick_and_an_override_can_move_a_picture():
    """a query under a region label is answered only from that region's folder, in a compare too, and with a picture
    of the place before one that is mainly of a person; and a "region" fix in the override file lets the owner put a
    picture in a region, or take it out"""
    shots, _, problems, _ = resolved({"beat": "card", "query": "trees", "label": "Mount Kalaga"},
                                     {"beat": "compare", "top": {"query": "swamp", "label": "Grassrivers"},
                                      "bottom": {"query": "skyscrapers", "label": "Vice City"}})
    nothing = resolved({"beat": "card", "query": "airboat", "label": "Vice City"})[2]
    # Under a region label a picture of the place comes before one that is mainly of a person, even a fresher one.
    crowd = SHOTS + "Grassrivers/Grassrivers_09.jpg"
    swamp = {**fake_tags(), crowd: tagged(crowd, "men with guns", ["man", "gun", "dock"], ["swamp"])}
    swamp.pop(GATOR)
    seen = {AIRBOAT: 1}
    labelled = picker.resolve_script(shot_script({"beat": "card", "query": "swamp", "label": "Grassrivers"}), swamp, seen)
    free = picker.resolve_script(shot_script({"beat": "card", "query": "swamp"}), swamp, seen)
    if (labelled[0]["lines"][0]["beats"][0]["file"] != "rockstar/" + AIRBOAT or len(labelled[3]) != 1
            or free[0]["lines"][0]["beats"][0]["file"] != "rockstar/" + crowd):
        return False
    tags = fake_tags()
    moved = picker.region_label_problem("Vice City", JASON, {**tags[JASON], "region": "Vice City"}, picker.load_regions())
    taken_out = picker.region_label_problem("MOUNT KALAGA", HELI, {**tags[HELI], "region": None}, picker.load_regions())
    return (not problems and shots[0]["file"] == "rockstar/" + DEER
            and shots[1]["top"]["file"] in ("rockstar/" + AIRBOAT, "rockstar/" + GATOR)
            and shots[1]["bottom"]["file"] == "rockstar/" + SKYLINE
            and len(nothing) == 1 and "in Vice City" in nothing[0]
            and moved is None and taken_out and "not from a region folder" in taken_out)


@check
def picker_refuses_what_it_cant_do():
    """a lock-on with a query (its box needs a known picture), a shot with both a file and a query, and a region that
    doesn't exist are each a problem that says what to change; a text card's "bg" can be a query"""
    lock = resolved({"beat": "lockon", "query": "helicopter", "box": [1, 2, 30, 40]})[2]
    both = resolved({"beat": "full", "file": "rockstar/" + DEER, "query": "deer"})[2]
    region = resolved({"beat": "full", "query": "deer", "region": "Los Santos"})[2]
    shots, picks, problems, _ = resolved({"beat": "count", "card": "6 REGIONS", "bg": {"query": "city skyline"}})
    return (len(lock) == 1 and "needs a file" in lock[0] and len(both) == 1 and "one of the two" in both[0]
            and len(region) == 1 and "Los Santos" in region[0] and "Grassrivers" in region[0]
            and not problems and shots[0]["bg"] == "rockstar/" + SKYLINE and picks[0]["side"] == "behind")


# --- The facts file ---

FACTS = {"date": {"id": "date", "claim": "The game launches 19 Nov 2026.", "safe_wording": "launches on November 19, 2026",
                  "status": "confirmed", "source": "https://example.com/date", "checked": "2026-11-11"},
         "size": {"id": "size", "claim": "The download is reportedly about 210 GB.", "safe_wording": "over 200 GB",
                  "also_ok": ["more than 200 GB"], "status": "reported", "source": "https://example.com/size",
                  "checked": "2026-11-11"},
         "regions": {"id": "regions", "claim": "Six confirmed regions.", "safe_wording": "six regions",
                     "status": "confirmed", "source": None, "checked": "2026-11-11"},
         "rumour": {"id": "rumour", "claim": "The map is twice the size.", "safe_wording": "twice the size",
                    "status": "unverified", "source": None, "checked": None}}


def fact_problems(say, ids="missing", shots=(), **top):
    line = {"say": say, "beats": list(shots)}
    if ids != "missing":
        line["facts"] = ids
    return facts.check_script({"title": "A test Short", "lines": [line], **top}, FACTS)[0]


@check
def a_line_must_list_real_checked_facts():
    """a line with no "facts" list, with an ID that isn't in facts.json, or with an unverified fact is refused, and
    the problem names the line and the fact; a line that lists a confirmed fact, or [] for no claim, passes"""
    missing = fact_problems("The game launches on November 19.")
    unknown = fact_problems("The game launches soon.", ["launch-day"])
    unverified = fact_problems("The map is twice the size.", ["rumour"])
    return (len(missing) == 2 and "needs \"facts\"" in missing[0] and "number 19" in missing[1]
            and len(unknown) == 1 and "no fact 'launch-day'" in unknown[0]
            and len(unverified) == 1 and "'rumour' is unverified" in unverified[0] and "line 1" in unverified[0]
            and fact_problems("The game launches on November 19.", ["date"]) == []
            and fact_problems("Let me explain.", []) == [])


@check
def a_reported_fact_must_use_its_safe_wording():
    """a reported fact passes only when its safe wording is in the line: said ("over 200 gigabytes" counts as "over
    200 GB"), shown on a card, or in a listed other wording; "212 gigabytes" and a plain "it is huge" are refused"""
    exact = fact_problems("You need 212 gigabytes free.", ["size"])
    vague = fact_problems("The download is huge.", ["size"])
    card_exact = fact_problems("The download is over 200 gigabytes.", ["size"], [{"beat": "count", "card": "212 GB"}])
    return (fact_problems("The download is over 200 gigabytes.", ["size"]) == []
            and fact_problems("It is big.", ["size"], [{"beat": "card", "card": "OVER|*200 GB*"}]) == []
            and fact_problems("It needs more than 200 gigs, so clear space.", ["size"]) == []
            and len(exact) == 2 and "safe wording" in exact[0] and "\"over 200 GB\"" in exact[0]
            and "number 212" in exact[1]
            and len(vague) == 1 and "safe wording" in vague[0]
            and len(card_exact) == 1 and "shows the number 212" in card_exact[0])


@check
def every_number_must_come_from_a_listed_fact():
    """a number said or shown (as digits or as a word, on a card, a label or a banner) must be in the wording of a
    fact that line lists; names such as GTA 6 and PS5 are not numbers; the title and description are checked too"""
    wrong_day = fact_problems("The game launches on November 20.", ["date"])
    word = fact_problems("There are seven regions.", ["regions"])
    label = fact_problems("Look at this.", ["regions"],
                          [{"beat": "card", "file": "x.jpg", "label": {"text": "8 regions", "style": "info"}}])
    title = facts.check_script({"title": "GTA 6 lands NOV 21...", "description": "Out on November 19, 2026.",
                                "lines": [{"say": "GTA 6 launches on November 19.", "facts": ["date"]}]}, FACTS)[0]
    return (len(wrong_day) == 1 and "number 20" in wrong_day[0]
            and len(word) == 1 and "number 7" in word[0]
            and len(label) == 1 and "shows the number 8" in label[0]
            and fact_problems("GTA 6 has six regions on PS5, and GTA 5 had fewer.", ["regions"],
                              [{"beat": "count", "card": "6 REGIONS"}]) == []
            and title == ["title: the number 21 is not in any fact the script lists"])


@check
def claims_sheet_lists_each_fact_with_its_source():
    """claims.txt puts each line next to its facts: the ID, the status in capitals, the claim, the source link (or
    "none given") and the date checked, with the safe wording for a reported fact"""
    script = {"title": "A test Short", "lines": [
        {"say": "The game launches on November 19.", "facts": ["date"], "beats": [{"beat": "card", "card": "NOV 19"}]},
        {"say": "It is over 200 gigabytes, across six regions.", "facts": ["size", "regions"]},
        {"say": "Let me explain.", "facts": []}]}
    problems, _, claims = facts.check_script(script, FACTS)
    text = facts.claims_text(script, claims, FACTS)
    return (problems == [] and "Line 1: The game launches on November 19." in text and "on screen: NOV 19" in text
            and "[date]  CONFIRMED" in text and "source: https://example.com/date" in text
            and "[size]  REPORTED" in text and "safe wording, which the line must use:  over 200 GB" in text
            and "source: none given" in text and "checked: 2026-11-11" in text
            and "facts: none" in text and "Facts used: 3" in text)


@check
def the_real_facts_file_reads_and_keeps_the_owners_statuses():
    """facts.json in the repo loads and every fact has the status the owner gave it (2026-11-12: nine confirmed, the
    Xbox pre-load time reported with a safe wording that says so, the rest unverified); digital pre-load is November
    10 and no usable fact says November 12 except as the old date or the boxed-copy date; every fact that may be used
    has a source link and the date it was checked"""
    real = facts.load_facts()
    confirmed = {"release-date", "preload-date", "preload-moved-to-nov-10", "boxed-copies-nov-12", "storage-212-gb",
                 "unlock-time", "six-regions", "thirteen-year-wait", "preload-no-early-play"}
    usable = {name: f for name, f in real.items() if f["status"] != "unverified"}
    says_12 = {name for name, f in usable.items()
               if any(day in facts.plain(w) for w in facts.wordings(f) for day in ("november 12", "12 nov"))}
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "facts.json"
        bad.write_text(json.dumps({"facts": [{"id": "a", "claim": "x", "safe_wording": "x", "status": "true"},
                                             {"id": "a", "claim": "x", "safe_wording": "", "status": "confirmed"}]}),
                       encoding="utf-8")
        broken = raises(facts.FactsError, facts.load_facts, bad)
    return ({name for name, f in real.items() if f["status"] == "confirmed"} == confirmed
            and {name for name, f in real.items() if f["status"] == "reported"} == {"preload-xbox-global"}
            and "reportedly" in real["preload-xbox-global"]["safe_wording"]
            and all("reportedly" in w for w in real["preload-xbox-global"]["also_ok"])
            and real["preload-date"]["safe_wording"] == "digital pre-load on PS5 started at midnight local time on November 10"
            and says_12 == {"preload-moved-to-nov-10", "boxed-copies-nov-12"}
            and "212" in facts.numbers_in(real["storage-212-gb"]["safe_wording"])
            and "ps5-download-size" not in real and "longest gap" not in real["thirteen-year-wait"]["claim"]
            and real["longest-gta-gap"]["status"] == "unverified"
            and all(f.get("checked") and f.get("source") for f in usable.values())
            and len(real) - len(usable) == 12
            and broken and "status is" in broken and "two facts have this id" in broken and "needs safe_wording" in broken)


# --- make_short.py ---

GOOD_TITLE = "GTA 6 pre-load starts SOONER than you think"      # 43 characters, one word in capitals


def short_script(**changes):
    """A whole script make_short.py accepts: 98 spoken words, real fact IDs of FACTS, two pictures asked for."""
    filler = "This line has no claim in it and only fills the time with plain words for the count."
    script = {"title": GOOD_TITLE, "topic": "a test topic", "topic_score": {"score": 4, "why": "every player needs it"},
              "description": "A test Short about the launch.", "hashtags": ["#GTA6", "#GTAVI", "#Shorts"],
              "voice": "am_puck", "speed": 1.1,
              "lines": [{"say": "The game launches on November 19. Here's why that matters.", "facts": ["date"],
                         "beats": [{"beat": "full", "query": "swamp airboat"},
                                   {"beat": "count", "card": "19 NOV"}]},
                        {"say": "The download is over 200 gigabytes, across six regions.", "facts": ["size", "regions"],
                         "beats": [{"beat": "card", "query": "deer", "label": "Mount Kalaga"}]},
                        *({"say": filler, "facts": [], "beats": [{"beat": "card", "card": "WAIT"}]} for _ in range(4)),
                        {"say": "So the game launches on November 19.", "facts": ["date"],
                         "beats": [{"beat": "card", "file": "rockstar/" + GATOR}]}]}
    script.update(changes)
    return script


def short_run(tmp, script, pacing=None, visuals=None, **more):
    """make_short on a script, with made-up facts and tags, tiny real pictures and a stand-in for the render."""
    tmp = Path(tmp)
    tags = fake_tags()
    for rel in (AIRBOAT, GATOR, DEER, HELI):
        path = tmp / "gta" / "rockstar" / rel
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=64x36", "-frames:v", "1",
                            str(path)], check=True)
        tags[rel]["size"] = [64, 36]
    rendered = []

    def render(resolved_path, out):
        rendered.append(json.loads(Path(resolved_path).read_text(encoding="utf-8")))
        (out / "short.mp4").write_bytes(b"not a real video")
        report = {"length_s": 36.0, "length_ok": True, "target_s": [34, 39], "warnings": [], **(pacing or {})}
        run_log = {"pacing": report, "warnings": [], **({"visuals": visuals} if visuals is not None else {})}
        (out / "log.json").write_text(json.dumps(run_log), encoding="utf-8")
        return lambda p: [f"  Pacing check: {p['length_s']} s"]
    path = tmp / "script.json"
    path.write_text(json.dumps(script), encoding="utf-8")
    lines = []
    try:
        out, ready = make_short.make_short(path, shorts_dir=tmp / "shorts", root=tmp / "gta", today="2026-11-11",
                                           facts=FACTS, tags=tags, render=render, log=lines.append, **more)
        error = None
    except make_short.ShortError as e:
        out, ready, error = None, False, str(e)
    return {"out": out, "ready": ready, "error": error, "rendered": rendered, "lines": lines, "shorts": tmp / "shorts"}


@check
def title_rules():
    """a title must be 32-46 characters with 1 or 2 words in ALL CAPS and no hashtag; it may end with "..." or "!?";
    GTA, PS5 and other names that are always capitals don't count as the capital words"""
    ok = ("GTA 6 pre-load starts SOONER than you think", "The 6 regions of GTA 6 are HUGE...",
          "What time does GTA 6 ACTUALLY unlock!?", "Your PS5 is NOT READY for GTA 6 day one")
    problems = make_short.title_problems
    return (all(problems(t) == [] for t in ok)
            and any("31 characters" in p for p in problems("GTA 6 pre-load starts NOV 10..."))
            and any("47 characters" in p for p in problems("GTA 6 pre-load starts SOONER than you think now"))
            and any("0 word(s) in ALL CAPS" in p for p in problems("GTA 6 on PS5 unlocks at midnight local time"))
            and any("3 word(s) in ALL CAPS" in p for p in problems("GTA 6 pre-load is LIVE and it is SO BIG"))
            and any("hashtag" in p for p in problems("GTA 6 pre-load starts SOONER #GTA6 #Shorts"))
            and problems("") == ["title is missing"] and problems(None) == ["title is missing"])


@check
def upload_text_needs_a_description_and_three_to_five_hashtags():
    """the description must be there and hold no hashtag; hashtags must be 3 to 5 different tags; a topic score must be
    1-5 with a reason, and under 3 it warns without stopping; upload.txt has the title, the description and the tags"""
    def problems(**changes):
        return make_short.upload_problems(short_script(**changes))
    low = make_short.check_script(short_script(topic_score={"score": 2, "why": "PC players only"}), FACTS, fake_tags())
    text = make_short.upload_text(short_script())
    return (problems() == []
            and len(problems(description="")) == 1 and len(problems(description="Out now #GTA6")) == 1
            and len(problems(hashtags=["#GTA6", "#GTAVI"])) == 1
            and len(problems(hashtags=["#GTA6", "#GTAVI", "#Shorts", "#PS5", "#Xbox", "#Rockstar"])) == 1
            and len(problems(hashtags=["#GTA6", "#gta6", "#Shorts"])) == 1
            and len(problems(hashtags=["GTA6", "#GTA VI", "#Shorts"])) == 1
            and len(problems(topic_score={"score": 7, "why": "x"})) == 1 and problems(topic_score=None) == []
            and low["problems"] == [] and any("topic score 2 of 5" in w for w in low["warnings"])
            and text == f"TITLE\n{GOOD_TITLE}\n\nDESCRIPTION\nA test Short about the launch.\n\n#GTA6 #GTAVI #Shorts\n")


@check
def word_count_is_checked_before_anything_renders():
    """a script far outside 95-105 spoken words (under 85 or over 115) stops before rendering; one a little outside
    only warns"""
    script = short_script()
    words = make_short.spoken_words(script)
    short = short_script(lines=script["lines"][:3])
    with tempfile.TemporaryDirectory() as tmp:
        run = short_run(tmp, short)
    near = short_script()
    near["lines"][2] = {"say": "This one is a short plain line now.", "facts": [], "beats": [{"beat": "card", "card": "WAIT"}]}
    result = make_short.check_script(near, FACTS, fake_tags())
    return (words == 98 and run["error"] and "37 spoken words" in run["error"] and run["rendered"] == []
            and not (run["shorts"]).exists()
            and result["problems"] == [] and result["words"] == 88
            and any("88 spoken words" in w for w in result["warnings"]))


@check
def make_short_writes_every_file_and_strips_its_own_settings():
    """a good script is rendered and its folder, <date>-<slug>, holds short.mp4, script.json (as given),
    script_resolved.json (what explainer.py got: no facts, description, hashtags or topic, every query now a file),
    upload.txt, claims.txt, picks.jpg and pacing.txt; it is READY, and its pictures go in the used-media log"""
    with tempfile.TemporaryDirectory() as tmp:
        script = short_script()
        run = short_run(tmp, script)
        out = run["out"]
        if run["error"] or out is None:
            raise AssertionError(run["error"])
        got = run["rendered"][0]
        names = sorted(p.name for p in out.iterdir())
        used = picker.load_used(run["shorts"] / "_used_media.json")
        shots = [s for line in got["lines"] for s in line["beats"]]
        return (out.name == "2026-11-11-gta-6-pre-load-starts-sooner-than-you-think" and run["ready"]
                and names == ["claims.txt", "log.json", "pacing.txt", "picks.jpg", "script.json", "script_resolved.json",
                              "short.mp4", "upload.txt"]
                and json.loads((out / "script.json").read_text(encoding="utf-8")) == script
                and not any(k in got for k in ("description", "hashtags", "topic", "topic_score"))
                and not any("facts" in line for line in got["lines"])
                and not any("query" in s or "region" in s for s in shots)
                and shots[0]["file"] == "rockstar/" + AIRBOAT and shots[2]["file"] == "rockstar/" + DEER
                and (out / "picks.jpg").stat().st_size > 2000
                and "[size]  REPORTED" in (out / "claims.txt").read_text(encoding="utf-8")
                and GOOD_TITLE in (out / "upload.txt").read_text(encoding="utf-8")
                and "Pacing check" in (out / "pacing.txt").read_text(encoding="utf-8")
                and [s["name"] for s in used] == [out.name] and set(used[0]["files"]) == {AIRBOAT, DEER, GATOR}
                and any(line.startswith("READY") for line in run["lines"]))


@check
def a_bad_fact_or_picture_stops_before_the_render():
    """an unverified fact, a reported fact said the exact way, a query nothing matches and a region label on the
    wrong picture are all listed together, nothing is rendered and no folder is made; --check renders nothing either"""
    script = short_script()
    script["lines"][0]["facts"] = ["date", "rumour"]
    script["lines"][1]["say"] = "The download is 212 gigabytes, across six regions."
    script["lines"][0]["beats"][0] = {"beat": "full", "query": "spaceship"}
    script["lines"][1]["beats"][0] = {"beat": "card", "file": "rockstar/" + GATOR, "label": "Mount Kalaga"}
    with tempfile.TemporaryDirectory() as tmp:
        run = short_run(tmp, script)
        dry = short_run(tmp, short_script(), check_only=True)
        error = run["error"] or ""
        return ("'rumour' is unverified" in error and "safe wording" in error and "number 212" in error
                and "nothing shows spaceship" in error and "is from Grassrivers" in error
                and run["rendered"] == [] and not run["shorts"].exists()
                and dry["error"] is None and dry["rendered"] == [] and dry["out"] is None and not dry["shorts"].exists())


@check
def a_short_outside_the_length_or_pacing_is_not_ready():
    """a render that comes out 41 s long, or with a pacing warning, still writes its files but is NOT READY, and its
    pictures are not logged as used, so making it again can pick the same ones"""
    with tempfile.TemporaryDirectory() as tmp:
        long = short_run(tmp, short_script(), pacing={"length_s": 41.0, "length_ok": False})
        slow = short_run(tmp, short_script(), pacing={"warnings": ["pacing: nothing changes for 3.6 s"]})
        return (long["out"] is not None and long["ready"] is False and (long["out"] / "short.mp4").is_file()
                and any(line.startswith("NOT READY") and "41.0 s" in line for line in long["lines"])
                and slow["ready"] is False and any("nothing changes for 3.6 s" in line for line in slow["lines"])
                and picker.load_used(long["shorts"] / "_used_media.json") == [])


@check
def a_picture_the_loop_ending_covers_is_not_counted_as_used():
    """when the render's log shows that a picture never got on screen (the loop ending took the last line's time), it
    is left out of the used-media log, so a later Short can still use it"""
    with tempfile.TemporaryDirectory() as tmp:
        shown = [{"source": f"rockstar/{AIRBOAT}@0.3,0.6", "behind": None},
                 {"source": "card:19 NOV", "behind": f"rockstar/{DEER}"}]
        run = short_run(tmp, short_script(), visuals=shown)
        used = picker.load_used(run["shorts"] / "_used_media.json")
        return (run["ready"] and set(used[0]["files"]) == {AIRBOAT, DEER} and (run["out"] / "picks.jpg").is_file())


@check
def the_next_short_avoids_the_last_ones_pictures():
    """after a Short is made, another Short asking for the same thing gets a different picture, while making the first
    Short again keeps its own"""
    with tempfile.TemporaryDirectory() as tmp:
        one = short_script()
        one["lines"][-1]["beats"][0] = {"beat": "card", "file": "rockstar/" + HELI}    # so the alligator stays unused
        first = short_run(tmp, one)
        other = json.loads(json.dumps(one))
        other["title"] = "GTA 6 has a swamp and it looks UNREAL..."
        other["lines"][0]["beats"][0] = {"beat": "full", "query": "swamp"}
        second = short_run(tmp, other)
        again = short_run(tmp, one)
        pick = lambda run: run["rendered"][0]["lines"][0]["beats"][0]["file"]
        return (not first["error"] and not second["error"] and pick(first) == "rockstar/" + AIRBOAT
                and pick(second) == "rockstar/" + GATOR and pick(again) == "rockstar/" + AIRBOAT
                and len(picker.load_used(first["shorts"] / "_used_media.json")) == 2)


def main():
    ok = True
    for fn in CHECKS:
        try:
            good, err = bool(fn()), ""
        except Exception as e:  # a missing function or field is a failure of this check, not of the run
            good, err = False, f"  ({type(e).__name__}: {e})"
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'} {fn.__name__}: {fn.__doc__}{err}")
    print("RESULT: " + ("PASS" if ok else "FAIL"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
