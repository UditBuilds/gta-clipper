"""Checks for explainer.py: the script checks, word timing, captions, the timeline, Ken Burns and short clips on small
made-up files, then two real renders of a 3-line script using ffmpeg-made test videos and a test image.

The test videos carry a loud 13.5 kHz tone, which a voice at Kokoro's 24 kHz can't contain (it stops at 12 kHz), so
any of that tone in the Short is game audio that got through. Their colours avoid the caption colours for the same
reason: green or red on screen can only come from a caption.

Each check prints PASS or FAIL with what it tests. A check that raises counts as FAIL (with the error). The last line is
RESULT: PASS or FAIL; exit code 0 or 1. Needs .venv-tts, the Kokoro model files and ffmpeg/ffprobe on PATH; about 1 minute.

Usage:  .venv-tts\\Scripts\\python.exe explainertest.py
"""

import functools
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

import explainer as ex

HERE = Path(__file__).resolve().parent
FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")
CHECKS = []
_TMP = tempfile.TemporaryDirectory()
ROOT = Path(_TMP.name)
OUT = ROOT / "out"
W, H, FPS = 1080, 1920, 30
BLUE, PURPLE, AMBER = (10, 60, 200), (90, 24, 154), (200, 150, 0)
GREEN, RED = (57, 255, 20), (255, 42, 42)
GAME_HZ, MUSIC_HZ = 13500, 15000
SAFE = (60, 959, 380, 1539)   # x1, x2, y1, y2 where captions may draw, in frame pixels
TOP_BAND = 370                # rows above every caption and card


def check(fn):
    CHECKS.append(fn)
    return fn


def hexcolour(rgb):
    return "0x{:02X}{:02X}{:02X}".format(*rgb)


def run_ffmpeg(*args):
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True, capture_output=True)


def stderr_of(*args):
    return subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-y", *map(str, args)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stderr


def tone(hz, seconds):
    return ["-f", "lavfi", "-i", f"sine=f={hz}:r=48000:d={seconds},volume=0.5"]


def make_video(name, colour, size, seconds, box=None, audio=True):
    """A flat colour with a cyan box. box is its x and y expressions; no tone with audio off."""
    w, h = size
    x, y = box or ("100+60*t", "60")
    graph = (f"color=c={hexcolour(colour)}:s={w}x{h}:r={FPS}:d={seconds}[bg];color=c=0x00E5FF:s=160x160:r={FPS}:d={seconds}[b];"
             f"[bg][b]overlay=x='{x}':y='{y}'[v]")
    audio_args = tone(GAME_HZ, seconds) if audio else []
    run_ffmpeg("-f", "lavfi", "-i", "anullsrc=r=48000:d=0.01", *audio_args, "-filter_complex", graph, "-map", "[v]",
               *(["-map", "1:a", "-c:a", "aac", "-b:a", "192k"] if audio else []),
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", ROOT / name)


@functools.cache
def assets():
    """The made-up visuals and music. Side bars on a.mp4 are outside what a 9:16 crop keeps, so they must not show."""
    make_video("plain_a.mp4", BLUE, (1920, 1080), 8, ("880+60*t", "120"))
    run_ffmpeg("-i", ROOT / "plain_a.mp4", "-vf", "drawbox=x=0:y=0:w=300:h=ih:color=0xFF00FF:t=fill,"
               "drawbox=x=iw-300:y=0:w=300:h=ih:color=0xFF00FF:t=fill", "-c:a", "copy", "-c:v", "libx264", "-preset",
               "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", ROOT / "a.mp4")
    make_video("b.mp4", PURPLE, (1280, 720), 8, ("560+40*t", "60"))
    # 1 s clips. stops.mp4's last and first frames differ a lot; rocks.mp4 comes back to where it started.
    make_video("stops.mp4", BLUE, (1080, 1920), 1, ("100+800*t", "400"), audio=False)
    make_video("rocks.mp4", BLUE, (1080, 1920), 1, ("460+40*sin(2*PI*t)", "880"), audio=False)
    # A photo: amber, with a white square right of centre inside what the crop keeps, and a left half to tell focus by.
    run_ffmpeg("-f", "lavfi", "-i", f"color=c={hexcolour(AMBER)}:s=1920x1080:d=0.04",
               "-vf", "drawbox=x=0:y=0:w=500:h=ih:color=0x00E5FF:t=fill,"
               "drawbox=x=1420:y=0:w=500:h=ih:color=0xFFFFFF:t=fill,"
               "drawbox=x=1060:y=300:w=40:h=40:color=0xFFFFFF:t=fill", "-frames:v", "1", ROOT / "shot.png")
    run_ffmpeg("-f", "lavfi", "-i", f"sine=f={MUSIC_HZ}:r=48000:d=2,volume=0.5", "-c:a", "pcm_s16le", ROOT / "music.wav")
    return True


def write_script(name, **fields):
    path = ROOT / name
    path.write_text(json.dumps(fields), encoding="utf-8")
    return path


def run_tool(script, out=OUT):
    return subprocess.run([sys.executable, str(HERE / "explainer.py"), str(script), "--root", str(ROOT), "--out", str(out)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def frame_at(path, seconds):
    """One frame as an RGB array, converted by the file's own colour tags."""
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{seconds:.3f}", "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(np.int32)


def frame_number(path, n):
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", "-f",
                          "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(np.int32)


def count_near(picture, rgb, tolerance=30):
    return int((np.abs(picture - np.array(rgb)).max(axis=2) <= tolerance).sum())


def probe(path):
    return json.loads(subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json",
                                      str(path)], capture_output=True, text=True, check=True).stdout)


def band_level(path, hz, start=0.0, seconds=None):
    """The loudest moment (dB) in a narrow band around hz."""
    span = ["-t", str(seconds)] if seconds else []
    err = stderr_of("-ss", start, *span, "-i", path, "-vn", "-af", f"bandpass=f={hz}:width_type=h:w=400,bandpass=f={hz}:width_type=h:w=400,volumedetect",
                    "-f", "null", "-")
    m = re.search(r"max_volume: (-?[\d.]+|-inf) dB", err)
    return float(m.group(1)) if m else -math.inf


# --- Reading the script ---

@check
def times_are_read():
    """@ times as seconds, m:ss or h:mm:ss, and anything else is not a time"""
    good = {"42": 42, "42.5": 42.5, "0:42": 42, "1:02:03": 3723, "0:07.5": 7.5}
    return all(ex.parse_time(k) == v for k, v in good.items()) and not any(
        ex.parse_time(b) is not None for b in ("abc", "1:75", "-3", "", "1:2:3:4"))


@check
def visuals_are_read():
    """video@time, image, image@framing, a card in capitals, and a file name with its own @"""
    assets()
    shutil.copy(ROOT / "b.mp4", ROOT / "b@home.mp4")
    a, b, c, d, e = (ex.parse_visual(v, ROOT) for v in ("b.mp4@0:03", "shot.png", "shot.png@0.25", "card:the end", "b@home.mp4"))
    return (a["kind"] == "video" and a["start"] == 3 and b["kind"] == "image" and b["focus"] == 0.5 and b["size"] == (1920, 1080)
            and c["focus"] == 0.25 and d == {"source": "card:the end", "kind": "card", "text": "THE END"}
            and e["kind"] == "video" and e["start"] == 0)


@check
def every_problem_stops_before_rendering():
    """a missing file, a time past the end, a bad time, a tag the line doesn't say, a typo and bad framing: all listed at once, exit 2, nothing written"""
    assets()
    out = ROOT / "should_stay_empty"
    script = write_script("bad.json", title="Bad", lines=[
        {"say": "One two three.", "visuals": ["nope.mp4", "b.mp4@9:00", "b.mp4@soon", "shot.png@2"], "green": ["FOUR"], "gren": []},
        {"say": "Four five six.", "visuals": []}])
    r = run_tool(script, out)
    wanted = ["nope.mp4", "only 8.0 s long", "bad time 'soon'", "after an image's @", "'FOUR' isn't in", "'gren'", "needs at least one visual"]
    return r.returncode == 2 and all(w in r.stderr for w in wanted) and not out.exists()


@check
def bad_json_and_bad_settings_stop():
    """not JSON, a Notepad byte order mark is fine, a speed out of range and a missing script are clear errors"""
    assets()
    not_json = ROOT / "nope.json"
    not_json.write_text("{ title: oops", encoding="utf-8")
    bom = ROOT / "bom.json"
    bom.write_text(json.dumps({"title": "T", "lines": [{"say": "Hi there.", "visuals": ["shot.png"]}]}), encoding="utf-8-sig")
    fast = write_script("fast.json", title="T", speed=9, lines=[{"say": "Hi.", "visuals": ["shot.png"]}])
    bad = run_tool(not_json), run_tool(fast), run_tool(ROOT / "absent.json")
    return (ex.load_script(bom, ROOT)["voice"] == ex.DEFAULT_VOICE and all(r.returncode == 2 for r in bad)
            and "not valid JSON" in bad[0].stderr and "speed" in bad[1].stderr and "can't read" in bad[2].stderr)


@check
def dollar_amounts_warn():
    """espeak says $70 as "dollar seventy", so the script gets a warning"""
    script = {"lines": [{"say": "It costs $70 today."}, {"say": "Seventy dollars."}]}
    warnings = ex.script_warnings(script)
    return len(warnings) == 1 and "70 dollars" in warnings[0]


# --- Word timing ---

# Real espeak output: the sentence as a whole, and each word alone. The sentence differs on purpose: "a" is said "uh", not
# "ay"; "of the" is one group; $70, 4K and 1,200 are several groups for one word; "to be" changes.
SENTENCE = "ɐ mˈæp ʌvðə stˈeɪt kˈɔsts dˈɑːlɚ sˈɛvənti ɪn fˈɔːɹ kˈeɪ, ɔːɹ wˈʌn θˈaʊzənd tˈuːhˈʌndɹɪd kˈɔɪnz æt θˈɜːɾi ˌɛfpˌiːˈɛs."
ALONE = ["ˈeɪ", "mˈæp", "ʌv", "ðə", "stˈeɪt", "kˈɔsts", "dˈɑːlɚ sˈɛvənti", "ˈɪn", "fˈɔːɹ kˈeɪ,", "ɔːɹ",
         "wˈʌn θˈaʊzənd tˈuːhˈʌndɹɪd", "kˈɔɪnz", "æt", "θˈɜːɾi", "ˌɛfpˌiːˈɛs."]


@check
def words_line_up_with_the_sentence():
    """on the hardest real sentence, every word lands on its own first sound, even inside a merged group"""
    starts = ex.word_starts(ALONE, list(SENTENCE))
    # The first sound of each word, in the sentence as spoken (the stress marks are skipped).
    first = "ɐ m ʌ ð s k d ɪ f ɔ w k æ θ ɛ".split()
    return starts is not None and len(starts) == len(ALONE) and [SENTENCE[i] for i in starts] == first


@check
def unrelated_phonemes_are_refused():
    """phonemes that don't match, or a word with no sounds, return None so the length split takes over"""
    return ex.word_starts(["xyz", "qqq"], list("ɐ mˈæp ʌvðə")) is None and ex.word_starts(["mˈæp", ""], list("mˈæp")) is None


# --- Captions ---

def fake_word(text, sentence=0, colour=None, tag=None, pause=False, start=0.0, end=0.0):
    return {"text": text, "key": text, "sentence": sentence, "colour": colour, "tag": tag, "pause": pause, "start": start, "end": end}


@check
def chunks_follow_the_rules():
    """2-4 words at a time, a new chunk at a sentence end and a comma, no lone last word, a tagged phrase never split"""
    words = [fake_word(w) for w in "ONE TWO THREE FOUR FIVE".split()]
    plain = ex.caption_groups(words)
    tag = ("green", "T", 0)
    tagged = [fake_word(w) for w in "A B C".split()] + [fake_word(w, colour="green", tag=tag) for w in ("X", "Y")] + [fake_word("Z")]
    comma = [fake_word("ONE"), fake_word("TWO", pause=True), fake_word("THREE"), fake_word("FOUR")]
    sentences = [fake_word("ONE"), fake_word("TWO", 1), fake_word("THREE", 1)]
    texts = lambda groups: [[w["text"] for w in g] for g in groups]
    return (texts(plain) == [["ONE", "TWO", "THREE"], ["FOUR", "FIVE"]]
            and texts(ex.caption_groups(tagged)) == [["A", "B", "C"], ["X", "Y", "Z"]]
            and texts(ex.caption_groups(comma)) == [["ONE", "TWO"], ["THREE", "FOUR"]]
            and texts(ex.caption_groups(sentences)) == [["ONE"], ["TWO", "THREE"]])


@check
def tagged_phrase_stays_on_one_line():
    """a two-line chunk breaks outside a tagged phrase, even when the middle of the phrase would balance the lines better"""
    tag = ("green", "JASON DUVAL", 1)
    words = [fake_word("LEADS"), fake_word("JASON", colour="green", tag=tag), fake_word("DUVAL", colour="green", tag=tag), fake_word("AND")]
    lines = ex.caption_text(words).split("\\N")
    return len(lines) == 2 and any("JASON" in line and "DUVAL" in line for line in lines)


@check
def tags_colour_the_right_words():
    """a tag matches whatever the case and punctuation, and only there"""
    words = [{"key": ex.word_key(t), "colour": None, "tag": None} for t in "212 gigabytes, free of 212 things".split()]
    ex.apply_tags(words, {"green": ["212 GIGABYTES"], "red": ["things"]})
    return [w["colour"] for w in words] == ["green", "green", None, None, None, "red"]


@check
def text_stays_in_the_safe_area():
    """the widest captions, open cards and cards, drawn in Anton, stay out of Shorts' top, bottom, left and right zones (real pixels)"""
    captions = [["WWWWWWWWWWWWWWWW"], ["MMMM", "WWWW", "MMMM", "WWWW"], ["CONGRATULATIONS,", "EVERYBODY"], ["212", "GIGABYTES"],
                ["GALLERY", "ALREADY", "SHOWS", "YOU"], ["ABSOLUTELY-UNBELIEVABLE-THING"]]
    titles = ["EVERY PLACE IN GTA 6", "WWWWWWWWWWWWW", "THE LONGEST WAIT IN THE WHOLE SERIES", "13 YEARS"]
    events = [ex.dialogue(2, i, i + 1, "Caption", ex.caption_text([fake_word(w) for w in c])) for i, c in enumerate(captions)]
    at = len(events)
    for y in (ex.OPEN_CARD_Y, ex.CARD_Y):
        for t in titles:
            events.append(ex.dialogue(1, at, at + 1, "Title", ex.title_text(t, y, ex.POP)))
            at += 1
    folder = ROOT / "safe"
    folder.mkdir(exist_ok=True)
    (folder / "t.ass").write_text(ex.ass_document(events), encoding="utf-8")
    shutil.copy(ex.FONT_FILE, folder)
    err = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r=10:d={at}",
                          "-vf", "ass=t.ass:fontsdir=.,bbox=min_val=24", "-f", "null", "-"], cwd=folder, capture_output=True,
                         text=True, encoding="utf-8", errors="replace").stderr
    boxes = [tuple(map(int, b)) for b in re.findall(r"x1:(-?\d+) x2:(\d+) y1:(\d+) y2:(\d+)", err)]
    x1, x2, y1, y2 = min(b[0] for b in boxes), max(b[1] for b in boxes), min(b[2] for b in boxes), max(b[3] for b in boxes)
    print(f"       text reaches x {x1}-{x2}, y {y1}-{y2} (allowed x {SAFE[0]}-{SAFE[1]}, y {SAFE[2]}-{SAFE[3]})")
    return len(boxes) > at and x1 >= SAFE[0] and x2 <= SAFE[1] and y1 >= SAFE[2] and y2 <= SAFE[3]


@check
def anton_is_really_used():
    """libass picks Anton when the font is there, and the check notices when it isn't"""
    good, bad = ROOT / "font_good", ROOT / "font_bad"
    good.mkdir(exist_ok=True)
    bad.mkdir(exist_ok=True)
    shutil.copy(ex.FONT_FILE, good)
    return ex.font_warning(good) is None and ex.font_warning(bad) is not None and (HERE / "fonts" / "OFL.txt").is_file()


# --- The timeline ---

def visual(name):
    return {"source": name, "kind": "card", "text": name}


@check
def slots_cover_the_voice_exactly():
    """no gap or overlap, visuals share a line evenly, and the loop ending is the first visual on the last 45 frames"""
    lines = [{"visuals": [visual("a"), visual("b")]}, {"visuals": [visual("c")]}, {"visuals": [visual("d"), visual("e")]}]
    slots = ex.plan_slots(lines, [0.0, 2.0, 4.0], 240)
    contiguous = all(a["end"] == b["start"] for a, b in zip(slots, slots[1:]))
    return (contiguous and slots[0]["start"] == 0 and slots[-1]["end"] == 240 and [s["frames"] for s in slots[:2]] == [30, 30]
            and slots[-1]["visual"]["source"] == "a" and slots[-1].get("loop_tail") and slots[-1]["frames"] == 45
            and sum(s["frames"] for s in slots) == 240)


@check
def tiny_leftover_joins_the_loop_ending():
    """a visual left with a sliver before the loop ending is dropped, and the ending starts where it did"""
    lines = [{"visuals": [visual("a")]}, {"visuals": [visual("b")]}]
    slots = ex.plan_slots(lines, [0.0, 3.0], 100)  # b would have 100-45-90 = -35 frames, a 55
    lines2 = [{"visuals": [visual("a")]}, {"visuals": [visual("b")]}, {"visuals": [visual("c")]}]
    slots2 = ex.plan_slots(lines2, [0.0, 2.0, 2.9], 112)  # b is cut to 60-67, 7 frames: dropped
    return ([s["visual"]["source"] for s in slots] == ["a", "a"] and [s["visual"]["source"] for s in slots2] == ["a", "a"]
            and slots2[-1]["start"] == 60 and slots2[-1]["frames"] == 52)


@check
def motions_vary_and_repeat_exactly():
    """all four moves appear, never the same twice running, speeds differ, and the same title gives the same plan"""
    def plan(seed):
        slots = [{"visual": {"kind": "image", "source": str(i)}, "frames": 60} for i in range(16)]
        ex.assign_motions(slots, seed)
        return [(s["motion"]["kind"], s["motion"]["speed"]) for s in slots]
    one, kinds = plan("title"), [k for k, _ in plan("title")]
    speeds = {s for _, s in one}
    return (set(kinds) == set(ex.MOTIONS) and all(a != b for a, b in zip(kinds, kinds[1:])) and len(speeds) > 8
            and min(speeds) >= ex.SPEED_RANGE[0] and max(speeds) <= ex.SPEED_RANGE[1]
            and plan("title") == one and plan("another") != one)


def marker_box(picture):
    """Where the white square is: its left, right and width."""
    ys, xs = np.nonzero((picture.min(axis=2) > 225))
    return int(xs.min()), int(xs.max()), int(xs.max() - xs.min())


def render_motion(motion, frames=60, source="shot.png"):
    assets()
    out = ROOT / f"motion_{motion['kind']}.mp4"
    slot = {"visual": ex.parse_visual(source, ROOT), "frames": frames, "motion": motion}
    ex.render_segment(slot, out, ex.pick_encoder()[1])
    return out


@check
def ken_burns_moves_the_way_it_says():
    """on real frames: zoom in grows the marker, zoom out shrinks it, pan left moves it right, pan right moves it left"""
    results = {}
    for kind in ex.MOTIONS:
        path = render_motion(ex.make_motion(kind, 1.0, 60))
        results[kind] = marker_box(frame_number(path, 0)), marker_box(frame_number(path, 59))
    print("       marker (left, right, width), first and last frame:", results)
    return (results["zoom_in"][1][2] > results["zoom_in"][0][2] + 3 and results["zoom_out"][1][2] < results["zoom_out"][0][2] - 3
            and results["pan_left"][1][0] > results["pan_left"][0][0] + 50 and results["pan_right"][1][0] < results["pan_right"][0][0] - 50)


@check
def image_loop_ending_lands_on_the_first_frame():
    """the loop ending on an image finishes exactly where the opening started, whichever way the opening moved"""
    worst = 0.0
    for kind in ex.MOTIONS:
        opening = ex.make_motion(kind, 1.1, 66)
        first = frame_number(render_motion(opening, 66), 0)
        tail = ex.return_motion(opening, 66, 45)
        last = frame_number(render_motion(tail, 45), 44)
        worst = max(worst, float(np.abs(first - last).mean()))
    print(f"       worst mean difference between the first and the last frame: {worst:.2f} of 255")
    return worst < 2.0


@check
def framing_picks_the_part_of_the_photo():
    """@0 shows the photo's left part and @1 its right part; both still fill the frame"""
    motion = {"kind": "zoom_in", "speed": 1.0, "zoom": (1.0, 1.0), "across": (0.5, 0.5)}
    left = frame_number(render_motion(motion, 2, "shot.png@0"), 0)
    right = frame_number(render_motion(motion, 2, "shot.png@1"), 0)
    return count_near(left, (0, 229, 255)) > W * H * 0.5 and count_near(right, (255, 255, 255)) > W * H * 0.5


def render_clip(name, frames, label):
    assets()
    v = ex.parse_visual(name, ROOT)
    slot = {"visual": v, "frames": frames, "start_s": 0.0, "fill": ex.fill_mode(v, 0.0, frames)}
    out = ROOT / f"clip_{label}.mp4"
    ex.render_segment(slot, out, ex.pick_encoder()[1])
    return slot["fill"], out


@check
def short_clip_that_cannot_loop_holds():
    """a 1 s clip whose ends don't match, in a 2 s slot: exactly 60 frames, moving at first, then frozen on its last frame"""
    fill, out = render_clip("stops.mp4", 60, "hold")
    a, b, c = frame_number(out, 10), frame_number(out, 40), frame_number(out, 59)
    return (fill == "hold" and int(probe(out)["streams"][0]["nb_read_frames"]) == 60
            and float(np.abs(a - b).mean()) > 0.5 and float(np.abs(b - c).mean()) < 0.1)


@check
def short_clip_that_can_loop_loops():
    """a 1 s clip that returns to its start, in a 3 s slot: exactly 90 frames, and it keeps moving through each repeat"""
    fill, out = render_clip("rocks.mp4", 90, "loop")
    a, b, c = frame_number(out, 7), frame_number(out, 37), frame_number(out, 52)
    return (fill == "loop" and int(probe(out)["streams"][0]["nb_read_frames"]) == 90
            and float(np.abs(a - b).mean()) < 0.1 and float(np.abs(b - c).mean()) > 0.3)


@check
def clip_long_enough_just_plays():
    """a clip with enough length is neither held nor looped"""
    assets()
    return ex.fill_mode(ex.parse_visual("b.mp4", ROOT), 0.0, 60) == "play"


# --- Two real renders ---

@functools.cache
def main_run():
    """A 3-line script with test videos, a test image and a card."""
    assets()
    script = write_script("three.json", title="Test: Three Lines!", voice="am_puck", speed=1.18, open_card="BIG TOPIC",
                          music=None, lines=[
        {"say": "The first line has two parts, and 212 gigabytes is one of them.", "visuals": ["a.mp4@0:03", "shot.png"],
         "green": ["212 GIGABYTES"], "red": []},
        {"say": "Danger is the word you should remember today. It comes up right here.", "visuals": ["b.mp4@1.5"], "green": [], "red": ["DANGER"]},
        {"say": "A map of the state comes last, so watch how the ending works.", "visuals": ["shot.png", "card:THE END"]}])
    result = run_tool(script)
    assert result.returncode == 0, result.stderr[-500:]
    folder = Path(re.search(r"Run folder: (.+)", result.stdout).group(1).strip())
    return folder, json.loads((folder / "log.json").read_text(encoding="utf-8")), result.stdout


def caption_starts(folder, needle=None):
    """The start time, in seconds, of each caption event, or just those holding the text."""
    out = []
    for line in (folder / "captions.ass").read_text(encoding="utf-8").splitlines():
        m = re.match(r"Dialogue: 2,(\d+):(\d+):(\d+\.\d+),", line)
        if m and (needle is None or needle in line):
            out.append(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)))
    return out


@check
def run_folder_is_as_asked():
    """<slug>/<YYYYMMDD-HHMMSS>/ with short.mp4, voice.wav, captions.ass, log.json and no leftovers"""
    folder = main_run()[0]
    names = sorted(p.name for p in folder.iterdir())
    return (folder.parent.name == "test-three-lines" and re.fullmatch(r"\d{8}-\d{6}", folder.name)
            and names == ["captions.ass", "log.json", "short.mp4", "voice.wav"])


@check
def short_is_1080x1920_at_30_fps():
    """H.264 1080x1920 at 30 fps in yuv420p, and one AAC stereo audio stream, nothing else"""
    info = probe(main_run()[0] / "short.mp4")
    video = [s for s in info["streams"] if s["codec_type"] == "video"]
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    v, a = video[0], audio[0]
    return (len(video) == 1 and len(audio) == 1 and len(info["streams"]) == 2 and (v["width"], v["height"]) == (W, H)
            and v["codec_name"] == "h264" and v["r_frame_rate"] == "30/1" and v["pix_fmt"] == "yuv420p"
            and a["codec_name"] == "aac" and a["channels"] == 2)


@check
def video_length_matches_the_voice():
    """the video is within 0.3 s of the voice, and its frame count is exact"""
    folder, log, _ = main_run()
    voice = float(probe(folder / "voice.wav")["format"]["duration"])
    info = probe(folder / "short.mp4")
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    video = float(info["format"]["duration"])
    print(f"       voice {voice:.3f} s, video {video:.3f} s, {v['nb_read_frames']} frames")
    return abs(video - voice) <= 0.3 and int(v["nb_read_frames"]) == log["durations"]["frames"] == round(voice * FPS)


@check
def captions_hold_the_tag_colours():
    """the .ass has 212 GIGABYTES in neon green and DANGER in red, and untagged words have no colour of their own"""
    text = (main_run()[0] / "captions.ass").read_text(encoding="utf-8")
    white_line = next(line for line in text.splitlines() if "THE FIRST" in line)
    return ("{\\c&H14FF39&}212" in text and "{\\c&H14FF39&}GIGABYTES" in text and "{\\c&H2A2AFF&}DANGER" in text
            and "\\c&H14FF39&" not in white_line and "\\c&H2A2AFF&" not in white_line and "Anton" in text)


@check
def colours_really_show_on_screen():
    """while a tagged chunk shows, the caption band has that exact green or red; while an untagged one shows, neither"""
    folder = main_run()[0]
    short = folder / "short.mp4"
    band = lambda seconds: frame_at(short, seconds + 0.25)[1100:1540]
    green = band(caption_starts(folder, "GIGABYTES")[0])
    red = band(caption_starts(folder, "DANGER")[0])
    plain = band(caption_starts(folder, "THE FIRST")[0])
    counts = count_near(green, GREEN), count_near(red, RED), count_near(plain, GREEN), count_near(plain, RED), count_near(green, RED)
    print(f"       green px {counts[0]}, red px {counts[1]}; untagged chunk: green {counts[2]}, red {counts[3]}")
    return counts[0] > 2000 and counts[1] > 2000 and counts[2] == counts[3] == counts[4] == 0


@check
def opening_card_shows_then_goes():
    """the open card is on screen for the first 2 s, above the captions, and gone after"""
    folder = main_run()[0]
    short = folder / "short.mp4"
    text = (folder / "captions.ass").read_text(encoding="utf-8")
    area = lambda seconds: frame_at(short, seconds)[465:868, 100:640]  # left of the test photo's white marker
    white_on, white_off = count_near(area(1.0), (255, 255, 255)), count_near(area(2.4), (255, 255, 255))
    print(f"       white pixels in the card's area: {white_on} at 1.0 s, {white_off} at 2.4 s")
    return "Dialogue: 1,0:00:00.00,0:00:02.00,Title" in text and "BIG\\NTOPIC" in text.replace(" ", "\\N") and white_on > 5000 and white_off < 50


@check
def video_fills_the_frame_with_no_blur_or_bars():
    """the first visual reaches every edge in its own colour, and its side bars are cropped away, not squeezed in"""
    picture = frame_at(main_run()[0] / "short.mp4", 0.1)
    edges = [picture[5, 5], picture[5, W - 6], picture[H - 6, 5], picture[H - 6, W - 6], picture[H // 2, 5], picture[H // 2, W - 6]]
    return all(np.abs(e - np.array(BLUE)).max() < 25 for e in edges) and count_near(picture, (255, 0, 255), 40) == 0


@check
def last_frames_show_the_first_visual():
    """over the last second the picture is the first visual again, not the card before the loop ending"""
    folder, log, _ = main_run()
    short = folder / "short.mp4"
    duration = log["durations"]["video_s"]
    before = log["visuals"][-2]["start_s"] + 0.1
    means = [frame_at(short, t)[:TOP_BAND].mean(axis=(0, 1)) for t in (duration - 1.0, duration - 0.5, duration - 0.05)]
    first = frame_at(short, 0.1)[:TOP_BAND].mean(axis=(0, 1))
    other = frame_at(short, before)[:TOP_BAND].mean(axis=(0, 1))
    print(f"       top band colour: first frame {first.round()}, last {means[-1].round()}, the visual before the ending {other.round()}")
    return (log["visuals"][-1]["loop_tail"] and log["visuals"][-1]["source"] == "a.mp4@0:03" and log["per_second"][-1]["visual"] == "a.mp4@0:03"
            and all(np.abs(m - first).max() < 8 for m in means) and np.abs(other - first).max() > 40)


@check
def no_game_audio_gets_through():
    """the test videos' tone is loud in the source, and the Short's band level is just the voice's own (renders differ by about 1 dB); game audio mixed in at -20 dB would clearly fail this"""
    folder = main_run()[0]
    has_audio = any(s["codec_type"] == "audio" for s in probe(ROOT / "a.mp4")["streams"])
    source = band_level(ROOT / "a.mp4", GAME_HZ)
    voice, short = band_level(folder / "voice.wav", GAME_HZ), band_level(folder / "short.mp4", GAME_HZ)
    leak = ROOT / "leak.mp4"
    run_ffmpeg("-i", folder / "short.mp4", "-i", ROOT / "a.mp4", "-filter_complex",
               "[1:a]volume=0.1,apad[g];[0:a][g]amix=inputs=2:normalize=0:duration=first[a]", "-map", "0:v", "-map", "[a]",
               "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", leak)
    leaked = band_level(leak, GAME_HZ)
    print(f"       {GAME_HZ} Hz band: {source:.1f} dB in the source, {voice:.1f} dB in the voice alone, {short:.1f} dB in the Short, "
          f"{leaked:.1f} dB if game audio leaked at -20 dB")
    return has_audio and short <= voice + 6 and leaked > voice + 6


@check
def voice_is_about_minus_14_lufs():
    """voice.wav and the finished Short both measure -14 LUFS within 1 LU, peaks under -1 dB"""
    folder = main_run()[0]
    voice, short = ex.measure_loudness(folder / "voice.wav"), ex.measure_loudness(folder / "short.mp4")
    print(f"       voice {voice[0]:.1f} LUFS, peak {voice[1]:.1f} dB; Short {short[0]:.1f} LUFS, peak {short[1]:.1f} dB")
    return abs(voice[0] + 14) <= 1 and abs(short[0] + 14) <= 1 and short[1] <= -1.0


def voice_samples(folder):
    import wave
    with wave.open(str(folder / "voice.wav")) as w:
        rate, channels, raw = w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())
    return np.frombuffer(raw, np.int16).reshape(-1, channels)[:, 0].astype(np.float32) / 32768, rate


def quiet_runs(samples, rate, db, least):
    """(start, end) in seconds of every stretch this far under the loudest moment, at least this long."""
    frame = int(0.01 * rate)
    count = len(samples) // frame
    rms = np.sqrt((samples[:count * frame].reshape(-1, frame) ** 2).mean(1))
    quiet = rms < rms.max() * 10 ** (db / 20)
    runs, start = [], None
    for i, q in enumerate(list(quiet) + [False]):
        if q and start is None:
            start = i
        elif not q and start is not None:
            runs.append((start, i))
            start = None
    joined = []  # a tick of 30 ms or less doesn't end a silence, or it would hide most of one from this check
    for a, b in runs:
        if joined and a - joined[-1][1] <= 3:
            joined[-1] = (joined[-1][0], b)
        else:
            joined.append((a, b))
    return [(a * 0.01, b * 0.01) for a, b in joined if (b - a) * 0.01 >= least]


@check
def dead_air_is_gone():
    """no silence in voice.wav lasts over 0.35 s, even with a tick inside it; straight from the model the gap after a sentence was about 0.6 s"""
    samples, rate = voice_samples(main_run()[0])
    runs = quiet_runs(samples, rate, -45, 0.05)
    longest = max((b - a for a, b in runs), default=0)
    print(f"       longest quiet stretch {longest:.2f} s")
    return longest <= 0.35


def sound_with_tail(tail_at, tail_length, tail_db, rate=24000):
    """1 s of speech-level noise, silence, then a short noise at tail_at seconds, with the audio ending 0.4 s after it."""
    rng = np.random.default_rng(1)
    audio = np.zeros(int((tail_at + tail_length + 0.4) * rate), dtype=np.float32)
    audio[:rate] = rng.uniform(-0.5, 0.5, rate)
    start, length = int(tail_at * rate), int(tail_length * rate)
    audio[start:start + length] = rng.uniform(-0.5, 0.5, length) * 10 ** (tail_db / 20)
    return audio, rate


@check
def sentence_tail_cut_is_careful():
    """a faint burst long after the speech goes, but a soft ending close to it, a loud late word and a long late sound all stay"""
    def end_s(*spec):
        audio, rate = sound_with_tail(*spec)
        return ex.speech_end(audio, rate) / rate
    tick = end_s(1.2, 0.03, -40)        # 200 ms after the speech: cut back to the speech
    soft = end_s(1.05, 0.03, -40)       # 50 ms after: the "th" of "myth", kept
    loud = end_s(1.2, 0.03, -10)        # far but loud: a real word
    long_late = end_s(1.2, 0.2, -40)    # far and faint, but too long to be a tick
    print(f"       ends at: tick {tick:.2f}, soft ending {soft:.2f}, loud {loud:.2f}, long {long_late:.2f} s")
    return tick < 1.06 and soft > 1.08 and loud > 1.23 and long_late > 1.4


@check
def captions_follow_the_real_speech():
    """the first caption comes within 0.15 s of the first sound, and after every real pause a caption starts within 0.15 s of the speech returning (from the audio, not the model's numbers)"""
    folder, log, _ = main_run()
    samples, rate = voice_samples(folder)
    starts = caption_starts(folder)
    runs = quiet_runs(samples, rate, -40, 0.07)
    first_sound = next((b for a, b in runs if a == 0), 0.0)
    resumes = [b for a, b in runs if a > 0 and b < len(samples) / rate - 0.1]
    misses = [round(r, 2) for r in resumes if min(abs(s - r) for s in starts) > 0.15]
    print(f"       first sound {first_sound:.2f} s, first caption {starts[0]:.2f} s; {len(resumes)} pauses, {len(misses)} with no caption near {misses}")
    return abs(starts[0] - first_sound) <= 0.15 and len(resumes) >= 2 and not misses and log["timing_method"] == "model phoneme durations"


@check
def log_says_what_happened():
    """the log has the durations, a visual for every second, each image's move, and the under-55 s warning"""
    folder, log, stdout = main_run()
    images = [v for v in log["visuals"] if v["kind"] == "image"]
    return (len(log["per_second"]) == math.ceil(log["durations"]["frames"] / FPS) and log["per_second"][0]["visual"] == "a.mp4@0:03"
            and all(v["motion"] for v in images) and log["voice"] == "am_puck" and log["speed"] == 1.18
            and any("target is 60-70 s" in w for w in log["warnings"]) and "WARNING" in stdout
            and {"voice_lufs", "final_lufs"} <= set(log["loudness"]))


@functools.cache
def music_run():
    assets()
    script = write_script("music.json", title="Music test", music="music.wav", lines=[
        {"say": "This short line has a low music bed under the voice, and it should loop until the very end of it.", "visuals": ["b.mp4"]}])
    result = run_tool(script)
    assert result.returncode == 0, result.stderr[-500:]
    folder = Path(re.search(r"Run folder: (.+)", result.stdout).group(1).strip())
    return folder, json.loads((folder / "log.json").read_text(encoding="utf-8"))


@check
def music_stays_well_under_the_voice():
    """named music plays low from start to end (a 2 s file loops), the mix stays near -14 LUFS, and no music named means none"""
    folder, log = music_run()
    short = folder / "short.mp4"
    length = log["durations"]["video_s"]
    start, end = band_level(short, MUSIC_HZ, 0.5, 1.5), band_level(short, MUSIC_HZ, length - 2.0, 1.2)
    none = band_level(main_run()[0] / "short.mp4", MUSIC_HZ)
    final = log["loudness"]["final_lufs"]
    print(f"       {MUSIC_HZ} Hz band: {start:.1f} dB near the start, {end:.1f} dB near the end, {none:.1f} dB with no music; mix {final:.1f} LUFS")
    return -50 < start < -24 and abs(start - end) < 3 and none < start - 25 and abs(final + 14) <= 1


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ok = True
    for fn in CHECKS:
        try:
            good, err = bool(fn()), ""
        except Exception as e:  # a missing function or field is a failure of this check, not of the run
            good, err = False, f"  ({type(e).__name__}: {e})"
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'} {fn.__name__}: {fn.__doc__}{err}", flush=True)
    print("RESULT: " + ("PASS" if ok else "FAIL"))
    _TMP.cleanup()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
