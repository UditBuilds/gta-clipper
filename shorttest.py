"""Checks for the topic-to-Short tools (PR 19): the picture tagger (tag_media.py) on made-up model answers.

No Ollama and no Rockstar files are used. ffmpeg makes two tiny pictures and a one-second video in a temporary folder,
and a stand-in answers for the vision model.

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


def tag_run(root, calls, answers=None, **flags):
    """One run of the tagger on root with a stand-in model. calls collects the frames it was asked about."""
    def ask(model, jpeg, capabilities):
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
