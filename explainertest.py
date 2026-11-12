"""Checks for explainer.py: the script checks, word timing, captions, the timeline, Ken Burns, focus, pans, card marks
and short clips, music picking, ducking and sound effects on small made-up files, then real renders of short scripts
using ffmpeg-made test videos, test images and test-tone music.

The test videos carry a loud 13.5 kHz tone, which a voice at Kokoro's 24 kHz can't contain (it stops at 12 kHz), so
any of that tone in the Short is game audio that got through. Their colours avoid the caption colours for the same
reason: cyan or red on screen can only come from a caption (their box and the photo's bar are a green the palette
doesn't use).

The renders that check the PR 14-16 pictures pin "layout": "full", "caption_style": "phrase" and "grade": false, which
is also the proof that old fields mean what they did. The PR 17 look has its own checks and its own render, and so do
the PR 18 beats (at the end of this file).

Each check prints PASS or FAIL with what it tests. A check that raises counts as FAIL (with the error). The last line is
RESULT: PASS or FAIL; exit code 0 or 1. Needs .venv-tts, the Kokoro model files and ffmpeg/ffprobe on PATH; about 3 minutes.

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
CYAN, RED = (0, 229, 255), (255, 42, 42)
BOX = (0, 200, 80)            # the test pictures' marker colour, outside the palette
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
    """A flat colour with a green box. box is its x and y expressions; no tone with audio off."""
    w, h = size
    x, y = box or ("100+60*t", "60")
    graph = (f"color=c={hexcolour(colour)}:s={w}x{h}:r={FPS}:d={seconds}[bg];color=c={hexcolour(BOX)}:s=160x160:r={FPS}:d={seconds}[b];"
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
               "-vf", f"drawbox=x=0:y=0:w=500:h=ih:color={hexcolour(BOX)}:t=fill,"
               "drawbox=x=1420:y=0:w=500:h=ih:color=0xFFFFFF:t=fill,"
               "drawbox=x=1060:y=300:w=40:h=40:color=0xFFFFFF:t=fill", "-frames:v", "1", ROOT / "shot.png")
    run_ffmpeg("-f", "lavfi", "-i", f"sine=f={MUSIC_HZ}:r=48000:d=2,volume=0.5", "-c:a", "pcm_s16le", ROOT / "music.wav")
    # A grey ramp, black at the left edge to white at the right, so a frame's mean brightness says where a pan is.
    run_ffmpeg("-f", "lavfi", "-i", "color=black:s=1920x1080:d=0.04", "-vf",
               "format=rgb24,geq=r='X*255/(W-1)':g='X*255/(W-1)':b='X*255/(W-1)'", "-frames:v", "1", ROOT / "ramp.png")
    # A picture narrower than the screen, which can't pan.
    run_ffmpeg("-f", "lavfi", "-i", f"color=c={hexcolour(AMBER)}:s=800x1600:d=0.04", "-frames:v", "1", ROOT / "tall.png")
    return True


def copy_fonts(folder):
    for font in ex.FONT_FILES:
        shutil.copy(font, folder)


OLD_LOOK = {"layout": "full", "caption_style": "phrase", "grade": False}   # the PR 14-16 pictures, for their checks


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


def low_level(path, start=0.0, seconds=None):
    """The loudest moment (dB) under about 70 Hz, where the hit's thump is and the voice has almost nothing."""
    span = ["-t", str(seconds)] if seconds else []
    err = stderr_of("-ss", start, *span, "-i", path, "-vn", "-af", ",".join(["lowpass=f=70"] * 4) + ",volumedetect",
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
def shot_objects_are_read():
    """the object form: focus by name, number or [across, down], a video's at with a focus, a pan, a weight, a marked card"""
    assets()
    a = ex.parse_visual({"file": "shot.png", "focus": "left"}, ROOT)
    b = ex.parse_visual({"file": "shot.png", "focus": [0.7, "top"], "weight": 2}, ROOT)
    c = ex.parse_visual({"file": "b.mp4", "at": "0:02", "focus": 0.3}, ROOT)
    d = ex.parse_visual({"file": "shot.png", "pan": "left"}, ROOT)
    e = ex.parse_visual({"card": "~nov 12~|*nov 10*", "weight": 1.5}, ROOT)
    f = ex.parse_visual("shot.png@right", ROOT)
    return (a["focus"] == 1 / 6 and a["focus_y"] == 0.5 and b["focus"] == 0.7 and b["focus_y"] == 1 / 6 and b["weight"] == 2
            and c["kind"] == "video" and c["start"] == 2 and c["focus"] == 0.3 and d["pan"] == "left" and "weight" not in d
            and e == {"source": "card:~nov 12~|*nov 10*", "kind": "card", "text": "~NOV 12~|*NOV 10*", "weight": 1.5}
            and f["focus"] == 5 / 6)


@check
def bad_shots_stop_before_rendering():
    """an unknown setting, a file and a card together, a pan on a video, on a narrow picture or with a focus, a bad focus or weight, an image with at, and card marks that don't pair up: all listed at once, exit 2, nothing written"""
    assets()
    out = ROOT / "bad_shots_out"
    bad = [{"file": "shot.png", "zoom": 2}, {"file": "shot.png", "card": "X"}, {"file": "b.mp4", "pan": "left"},
           {"file": "tall.png", "pan": "right"}, {"file": "shot.png", "pan": "right", "focus": 0.2},
           {"file": "shot.png", "focus": 1.5}, {"file": "shot.png", "focus": [0.5, "up"]}, {"file": "shot.png", "weight": 0},
           {"file": "shot.png", "at": "0:01"}, {"card": "~NOV 12|NOV 10~"}, {"card": "A|B|C|D"}, {"card": "X", "focus": 0.5},
           {"file": "shot.png", "pan": "up"}]
    script = write_script("bad_shots.json", title="Bad shots", lines=[{"say": "One two three.", "visuals": bad}])
    r = run_tool(script, out)
    wanted = ["unknown setting 'zoom'", "a file or a card", "pan is for images", "no wider than the screen",
              "takes no focus", "focus is where the subject is", "weight is its share", "an image has none",
              "isn't closed on its own line", "at most 3 lines", "a card takes only", "pan is right"]
    missing = [w for w in wanted if w not in r.stderr]
    if missing:
        print(f"       missing from the errors: {missing}")
    return r.returncode == 2 and not missing and r.stderr.count("line 1:") >= len(bad) and not out.exists()


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


@functools.cache
def music_folder():
    """ROOT/music with a 20 s and a 2 s track (the music test tone) and a file that isn't audio."""
    assets()
    folder = ROOT / ex.MUSIC_DIR
    folder.mkdir(exist_ok=True)
    run_ffmpeg(*tone(MUSIC_HZ, 20), "-c:a", "pcm_s16le", folder / "long.wav")
    run_ffmpeg(*tone(MUSIC_HZ, 2), "-c:a", "pcm_s16le", folder / "short.wav")
    (folder / "notes.txt").write_text("not audio", encoding="utf-8")
    return folder


def one_line(**fields):
    return dict(title="T", lines=[{"say": "Hi there.", "visuals": ["shot.png"]}], **fields)


@check
def sound_settings_are_read():
    """music by name (the music folder first, then the root), "auto" in any case, null or false; music_volume and sfx; with nothing set: no music, volume 1, sound effects on"""
    music_folder()
    read = lambda **fields: ex.load_script(write_script("sound.json", **one_line(**fields)), ROOT)
    default, named, rooted = read(), read(music="long.wav", music_volume=0.5, sfx=False), read(music="music.wav")
    auto, off, none = read(music="Auto"), read(music=False), read(music=None)
    return (default["music"] is None and default["music_volume"] == 1.0 and default["sfx"] is True
            and named["music"] == ROOT / "music" / "long.wav" and named["music_volume"] == 0.5 and named["sfx"] is False
            and rooted["music"] == ROOT / "music.wav" and auto["music"] == "auto" and off["music"] is None
            and none["music"] is None)


@check
def bad_sound_settings_stop():
    """an unknown voice (the error lists only the English voices), a missing music file, a music_volume too big or not a number, music that isn't text and sfx that isn't true or false: all listed, exit 2, nothing written"""
    music_folder()
    out = ROOT / "bad_sound_out"
    first = run_tool(write_script("bad_sound.json", **one_line(voice="am_nobody", music="missing.mp3", music_volume=5,
                                                              sfx="yes")), out)
    second = run_tool(write_script("bad_sound2.json", **one_line(music=42, music_volume="loud")), out)
    wanted = ["voice 'am_nobody' doesn't exist", "af_heart", "bm_george", "music file not found: 'missing.mp3'",
              "music_volume is how loud", "sfx is true"]
    missing = [w for w in wanted if w not in first.stderr]
    missing += [w for w in ("music is a file in", "music_volume is how loud") if w not in second.stderr]
    if missing:
        print(f"       missing from the errors: {missing}")
    return (first.returncode == second.returncode == 2 and not missing and "ef_dora" not in first.stderr
            and not out.exists())


@check
def auto_music_picks_from_the_music_folder():
    """"auto" picks a track from the music folder, the same one each time for a title, a long enough one when there is one, any of them when none is; an empty or missing folder means none; a named file is used as it is"""
    music_folder()
    a, b = ex.pick_music("auto", ROOT, 10, "one title"), ex.pick_music("auto", ROOT, 10, "another title")
    again = [ex.pick_music("auto", ROOT, 30, "same")[0] for _ in range(3)]
    spread = {ex.pick_music("auto", ROOT, 30, f"title {i}")[0].name for i in range(20)}
    empty = ROOT / "empty_root"
    (empty / ex.MUSIC_DIR).mkdir(parents=True, exist_ok=True)
    named = ex.pick_music(ROOT / "music.wav", ROOT, 10, "x")
    print(f"       10 s Short: {a[0].name} ({a[1]}); 30 s Shorts over 20 titles use {sorted(spread)}")
    return (a[0].name == b[0].name == "long.wav" and "2 track(s)" in a[1] and len(set(again)) == 1
            and spread == {"long.wav", "short.wav"} and ex.pick_music("auto", empty, 10, "x") == (None, None)
            and ex.pick_music("auto", ROOT / "nowhere", 10, "x") == (None, None)
            and named == (ROOT / "music.wav", "named in the script") and ex.pick_music(None, ROOT, 10, "x") == (None, None))


@check
def music_dips_under_speech_and_lifts_in_long_pauses():
    """the music stays down through speech and through a 0.2 s sentence break, comes up 4 dB in a 0.8 s pause, and is all the way down again before the speech comes back"""
    rng = np.random.default_rng(3)
    speech = lambda s: rng.uniform(-0.3, 0.3, int(s * ex.RATE)).astype(np.float32)
    quiet = lambda s: np.zeros(int(s * ex.RATE), np.float32)
    curve = ex.duck_curve(np.concatenate([speech(1), quiet(0.2), speech(1), quiet(0.8), speech(1)]))
    at = lambda s: float(curve[int(round(s / 0.01))])
    print(f"       lift at 1.1 s (short break) {at(1.1):.2f} dB, 2.6 s (long pause) {at(2.6):.2f} dB, 2.98 s {at(2.98):.2f} dB")
    return (curve[:220].max() == 0 and abs(at(2.6) - ex.GAP_LIFT_DB) < 1e-6 and at(2.98) == 0
            and curve[300:].max() == 0 and len(curve) == 400)


@check
def sound_effects_follow_lines_pans_and_cards():
    """a whoosh when a line starts or a pan begins, a hit when a card appears (it wins over a whoosh at the same moment or just before), nothing on the first frame or in the loop ending"""
    image, pan = {"source": "img", "kind": "image"}, {"source": "ramp.png (pan right)", "kind": "image", "pan": "right"}
    card = visual("card:X")
    lines = [{"visuals": [image, card]}, {"visuals": [image, pan]}, {"visuals": [card, image]}, {"visuals": [pan]}]
    events = ex.plan_sfx(ex.plan_slots(lines, [0, 2, 4, 6], 9 * FPS))
    close = ex.plan_sfx([{"line": 0, "start": 0, "end": 60, "visual": image}, {"line": 1, "start": 60, "end": 66, "visual": image},
                         {"line": 1, "start": 66, "end": 120, "visual": card}])
    got = [(e["kind"], e["at_s"]) for e in events]
    print(f"       {got}; a line starting 0.2 s before a card: {[(e['kind'], e['at_s']) for e in close]}")
    return (got == [("hit", 1.0), ("whoosh", 2.0), ("whoosh", 3.0), ("hit", 4.0), ("whoosh", 6.0)]
            and [(e["kind"], e["at_s"]) for e in close] == [("hit", 2.2)] and "line 4 starts" in events[-1]["why"])


def momentary_max(path):
    """The loudest 400 ms of a file (EBU R128 momentary loudness), in LUFS."""
    err = stderr_of("-i", path, "-af", "ebur128", "-f", "null", "-")
    return max(float(x) for x in re.findall(r"M:\s*(-?[\d.]+)", err))


@check
def sound_effects_sit_under_the_voice():
    """the loudest 400 ms of a whoosh is 10-16 LU under the voice's -14 LUFS and of a hit 6-12 LU under; neither has anything at the test tones; a hit starts and a whoosh peaks on its moment"""
    def level(sound, name):  # stereo, the same on both sides, as it goes into the mix
        import wave
        path = ROOT / name
        padded = np.concatenate([np.zeros(ex.RATE // 2, np.float32), sound, np.zeros(ex.RATE, np.float32)])
        with wave.open(str(path), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(ex.RATE)
            w.writeframes((np.repeat(padded[:, None], 2, 1) * 32767).astype(np.int16).tobytes())
        return momentary_max(path), band_level(path, GAME_HZ), band_level(path, MUSIC_HZ)
    whoosh, hit = level(ex.whoosh(0), "whoosh.wav"), level(ex.hit(0), "hit.wav")
    track = ex.sfx_track([{"kind": "hit", "at_s": 1.0}, {"kind": "whoosh", "at_s": 2.0}], 3 * ex.RATE)
    loud = np.abs(track)
    hit_start = np.flatnonzero(loud > loud[:int(1.5 * ex.RATE)].max() * 0.05)[0] / ex.RATE
    envelope = np.convolve(loud ** 2, np.ones(480) / 480, "same")
    whoosh_peak = (int(1.5 * ex.RATE) + np.argmax(envelope[int(1.5 * ex.RATE):])) / ex.RATE
    print(f"       loudest 400 ms: whoosh {whoosh[0]:.1f} LUFS, hit {hit[0]:.1f} LUFS (voice -14); at the test tones "
          f"{max(whoosh[1:] + hit[1:]):.0f} dB; hit starts {hit_start:.3f} s (1.0), whoosh peaks {whoosh_peak:.3f} s (2.0)")
    return (-30 <= whoosh[0] <= -24 and -26 <= hit[0] <= -20 and max(whoosh[1:] + hit[1:]) < -70
            and abs(hit_start - 1.0) < 0.003 and abs(whoosh_peak - 2.0) < 0.06)


@check
def dollar_amounts_warn():
    """espeak says $70 as "dollar seventy", so the script gets a warning"""
    script = {"lines": [{"say": "It costs $70 today."}, {"say": "Seventy dollars."}]}
    warnings = ex.script_warnings(script)
    return len(warnings) == 1 and "70 dollars" in warnings[0]


@check
def speed_and_length_warn():
    """a voice faster than 1.1 warns (1.1 itself doesn't); the target is 34-39 s since PR 18 (it was 40-50 s, and only over it warned): a Short over 39 s warns, one under 34 s warns, and 34-39 s doesn't"""
    fast = ex.script_warnings({"speed": 1.18, "lines": []})
    edge = ex.script_warnings({"speed": 1.1, "lines": []})
    return (len(fast) == 1 and "faster than 1.1" in fast[0] and not edge and ex.runtime_warnings(39.5)
            and "over" in ex.runtime_warnings(39.5)[0] and ex.runtime_warnings(33.5) and "under" in ex.runtime_warnings(33.5)[0]
            and not ex.runtime_warnings(34) and not ex.runtime_warnings(37.6) and not ex.runtime_warnings(39)
            and "under" in ex.runtime_note(33.5) and "inside" in ex.runtime_note(37.6) and "over" in ex.runtime_note(39.5)
            and (ex.MIN_TOTAL_S, ex.MAX_TOTAL_S) == (34, 39))


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
    """the widest phrase captions (Anton), and open cards and cards (Lilita One, counting numbers too), stay out of Shorts' top, bottom, left and right zones (real pixels)"""
    captions = [["WWWWWWWWWWWWWWWW"], ["MMMM", "WWWW", "MMMM", "WWWW"], ["CONGRATULATIONS,", "EVERYBODY"], ["212", "GIGABYTES"],
                ["GALLERY", "ALREADY", "SHOWS", "YOU"], ["ABSOLUTELY-UNBELIEVABLE-THING"]]
    titles = ["EVERY PLACE IN GTA 6", "WWWWWWWWWWWWW", "THE LONGEST WAIT IN THE WHOLE SERIES", "13 YEARS",
              "~NOV 12~|*NOV 10*", "*NOV 10*|12 AM LOCAL", "~WWWWWWWWWWWWWWWW~", "MMMM|~WWWWWWWWWWWW~|*MMMMMMMMMMMMM*",
              "1,200,000 PLAYERS", "212 GB"]
    events = [ex.dialogue(2, i, i + 1, "Caption", ex.caption_text([fake_word(w) for w in c])) for i, c in enumerate(captions)]
    at = len(events)
    for t in titles:
        events += ex.open_card_events(t, at, at + 1)
        events += ex.title_events(0, at + 1, at + 2, t, ex.CARD_Y, ex.POP, count=True)
        at += 2
    folder = ROOT / "safe"
    folder.mkdir(exist_ok=True)
    (folder / "t.ass").write_text(ex.ass_document(events), encoding="utf-8")
    copy_fonts(folder)
    err = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r=10:d={at}",
                          "-vf", "ass=t.ass:fontsdir=.,bbox=min_val=24", "-f", "null", "-"], cwd=folder, capture_output=True,
                         text=True, encoding="utf-8", errors="replace").stderr
    boxes = [tuple(map(int, b)) for b in re.findall(r"x1:(-?\d+) x2:(\d+) y1:(\d+) y2:(\d+)", err)]
    x1, x2, y1, y2 = min(b[0] for b in boxes), max(b[1] for b in boxes), min(b[2] for b in boxes), max(b[3] for b in boxes)
    print(f"       text reaches x {x1}-{x2}, y {y1}-{y2} (allowed x {SAFE[0]}-{SAFE[1]}, y {SAFE[2]}-{SAFE[3]})")
    return len(boxes) > at and x1 >= SAFE[0] and x2 <= SAFE[1] and y1 >= SAFE[2] and y2 <= SAFE[3]


GREY = 128


def ink_boxes(events, count, name):
    """Each event drawn over mid grey, one a second from second 0, and the box (x1, x2, y1, y2) around everything that
    isn't grey half a second in, after the pop: the fill, the black outline and the shadow."""
    folder = ROOT / name
    folder.mkdir(exist_ok=True)
    (folder / "t.ass").write_text(ex.ass_document(events), encoding="utf-8")
    copy_fonts(folder)
    raw = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"color=0x808080:s={W}x{H}:r=2:d={count}", "-vf",
                          "ass=t.ass:fontsdir=.,select='mod(n\\,2)'", "-fps_mode", "passthrough", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], cwd=folder, capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)
    boxes = []
    for frame in frames:
        ink = (np.abs(frame.astype(np.int16) - GREY).max(axis=2) > 12)
        rows, cols = np.flatnonzero(ink.any(axis=1)), np.flatnonzero(ink.any(axis=0))
        boxes.append((cols[0], cols[-1], rows[0], rows[-1]) if len(rows) else None)
    return boxes


@check
def outline_and_shadow_stay_in_the_safe_area_and_off_the_cards():
    """drawn over grey, so the black outline and the shadow count too: the widest captions stay in Shorts' safe area, and a two-line caption's top sits below the bottom of the tallest open card and its crossed-out bar (no caption shows over a text card since PR 17)"""
    captions = [["WWWWWWWWWWWWWWWW"], ["MMMM", "WWWW", "MMMM", "WWWW"], ["CONGRATULATIONS,", "EVERYBODY"],
                ["ABSOLUTELY-UNBELIEVABLE-THING"], ["GALLERY", "ALREADY", "SHOWS", "YOU"]]
    tall = ["MMMM|~WWWWWWWWWWWW~|*MMMMMMMMMMMMM*", "~WWWWWWWWWWWWWWWW~|MMMM|WWWW", "THE LONGEST WAIT IN THE WHOLE SERIES"]
    events = [ex.dialogue(2, i, i + 1, "Caption", ex.caption_text([fake_word(w) for w in c])) for i, c in enumerate(captions)]
    at = len(events)
    for t in tall:
        events += ex.open_card_events(t, at, at + 1)
        at += 1
    boxes = ink_boxes(events, at, "ink")
    caption_boxes, card_boxes = boxes[:len(captions)], boxes[len(captions):]
    x1, x2 = min(b[0] for b in caption_boxes), max(b[1] for b in caption_boxes)
    y1, y2 = min(b[2] for b in caption_boxes), max(b[3] for b in caption_boxes)
    two_line_top = min(b[2] for b, c in zip(caption_boxes, captions) if len(ex.caption_text([fake_word(w) for w in c]).split("\\N")) == 2)
    card_bottom = max(b[3] for b in card_boxes)
    print(f"       captions with outline and shadow reach x {x1}-{x2}, y {y1}-{y2} (allowed x {SAFE[0]}-{SAFE[1]}, "
          f"y {SAFE[2]}-{SAFE[3]}); lowest card ink y {card_bottom}, highest two-line caption ink y {two_line_top}")
    return (None not in boxes and x1 >= SAFE[0] and x2 <= SAFE[1] and y1 >= SAFE[2] and y2 <= SAFE[3]
            and card_bottom < two_line_top)


@check
def anton_is_really_used():
    """libass picks Anton and Lilita One when the fonts are there, the check notices when either isn't, and both licences are in the repo"""
    good, bad, half = ROOT / "font_good", ROOT / "font_bad", ROOT / "font_half"
    for folder in (good, bad, half):
        folder.mkdir(exist_ok=True)
    copy_fonts(good)
    shutil.copy(ex.FONT_FILE, half)
    missing = ex.font_warning(half)
    print(f"       with Anton alone: {missing}")
    return (ex.font_warning(good) is None and ex.font_warning(bad) is not None and missing is not None
            and "Lilita One" in missing and (HERE / "fonts" / "OFL.txt").is_file()
            and (HERE / "fonts" / "LilitaOne-OFL.txt").is_file())


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
def the_loop_ending_never_takes_a_pictures_whole_time():
    """no visual is dropped for the loop ending (PR 19: before, a last line shorter than the 1.5 s ending lost its picture, and a shot left with under 0.4 s was dropped): a shot the ending would cover starts earlier and gets 1.0 s, taken from the shot before it; if that one is left under 1.0 s it moves up the same way; shots the ending doesn't reach keep their own cuts; the ending is still the first visual on the last 45 frames, with no gap or overlap"""
    # The unlock-time Short of 2026-11-11: its last line, "Just remember one thing:", lasted 1.3 s of a 34.4 s Short.
    lines = [{"visuals": [visual("a"), visual("b")]}, {"visuals": [visual("c"), visual("d")]}, {"visuals": [visual("last")]}]
    slots = ex.plan_slots(lines, [0.0, 28.0, 33.1], 1032)
    by = {s["visual"]["source"]: s for s in slots if not s.get("loop_tail")}
    whole = lambda found, total: (all(a["end"] == b["start"] for a, b in zip(found, found[1:])) and found[0]["start"] == 0
                                  and found[-1]["end"] == total and sum(s["frames"] for s in found) == total)
    # Two pictures on a last line the ending covers completely, after a shot of only 1.2 s: all three move up.
    lines2 = [{"visuals": [visual("a")]}, {"visuals": [visual("b")]}, {"visuals": [visual("c"), visual("d")]}]
    slots2 = ex.plan_slots(lines2, [0.0, 6.0, 7.2], 270)        # c and d would start at 216 and 243; the ending at 225
    spans2 = [(s["visual"]["source"], s["start"], s["end"]) for s in slots2]
    # The two cases the old rule dropped a picture in.
    old1 = ex.plan_slots([{"visuals": [visual("a")]}, {"visuals": [visual("b")]}], [0.0, 3.0], 100)
    old2 = ex.plan_slots([{"visuals": [visual("a")]}, {"visuals": [visual("b")]}, {"visuals": [visual("c")]}], [0.0, 2.0, 2.9], 112)
    sources = lambda found: [s["visual"]["source"] for s in found]
    return (ex.MIN_SHOWN_FRAMES == 30 and sources(slots) == ["a", "b", "c", "d", "last", "a"]
            and by["last"]["frames"] == 30 and (by["last"]["start"], by["last"]["end"]) == (957, 987)
            and by["d"]["end"] == 957 and by["d"]["frames"] >= 30
            and (by["a"]["start"], by["a"]["end"], by["b"]["end"], by["c"]["end"]) == (0, 420, 840, 916)
            and slots[-1].get("loop_tail") and slots[-1]["frames"] == 45 and whole(slots, 1032)
            and spans2 == [("a", 0, 135), ("b", 135, 165), ("c", 165, 195), ("d", 195, 225), ("a", 225, 270)]
            and whole(slots2, 270)
            and sources(old1) == ["a", "b", "a"] and sources(old2) == ["a", "b", "c", "a"]
            and all(s["frames"] > 0 for s in old1 + old2) and whole(old1, 100) and whole(old2, 112)
            and old1[-1]["frames"] == 45 and old2[-1]["frames"] == 45)


@check
def a_short_last_line_keeps_the_pacing_rules():
    """with the unlock-time Short's own cuts (34.4 s, 17 shots, a 1.3 s last line), giving the last picture its 1.0 s adds one change and breaks no pacing rule: 4-6 changes every 10 s, no gap over 3 s, no shot over 2.5 s made longer, and the last line's pictures are on screen for 1.0 s or more, so its "shows for only" warning is gone"""
    starts = [0.0, 6.6, 12.6, 20.4, 26.5, 29.4, 33.1]
    shots = [3, 3, 4, 3, 1, 2, 1]
    lines = [{"visuals": [visual(f"{n}.{i}") for i in range(count)]} for n, count in enumerate(shots)]
    slots = ex.plan_slots(lines, starts, 1032)
    report = ex.pacing_report(ex.visual_changes(slots, 1032), 1032 / FPS)
    last = [s for s in slots if s["line"] == 6 and not s.get("loop_tail")]
    last_frames = sum(s["frames"] for s in last)
    own = [s for s in slots if not s.get("loop_tail")]
    print(f"       {len(own)} shots, {report['changes']} changes, {report['per_10_s']} per 10 s, longest gap "
          f"{report['longest_gap_s']} s; the last line's picture: {last_frames / FPS:.1f} s")
    return (len(own) == 17 and [s["visual"]["source"] for s in last] == ["6.0"] and last_frames >= ex.MIN_SHOWN_FRAMES
            and report["warnings"] == [] and 4.0 <= report["per_10_s"] <= 6.0 and report["longest_gap_s"] <= 3.0
            and not any("shows for only" in w or "show for only" in w for w in ex.slot_warnings(slots, lines, last_frames))
            and all(s["frames"] >= ex.MIN_SHOWN_FRAMES for s in own))


@check
def motions_vary_and_repeat_exactly():
    """all four moves appear, never the same twice running, speeds differ, and the same title gives the same plan"""
    def plan(seed):
        slots = [{"visual": {"kind": "image", "source": str(i), "size": (1920, 1080), "focus": 0.5}, "frames": 60}
                 for i in range(16)]
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
    motion = {"kind": "zoom_in", "speed": 1.0, "zoom": (1.0, 1.0), "centre": (0.5, 0.5)}
    left = frame_number(render_motion(motion, 2, "shot.png@0"), 0)
    right = frame_number(render_motion(motion, 2, "shot.png@1"), 0)
    return count_near(left, BOX) > W * H * 0.5 and count_near(right, (255, 255, 255)) > W * H * 0.5


def still_of(visual, zoom=1.0):
    """The first frame of an image shot held at this zoom, centred the way assign_motions would centre a zoom."""
    subject, middle = ex.crop_box(visual)[4:]
    motion = {"kind": "zoom_in", "speed": 1.0, "zoom": (zoom, zoom), "centre": (subject, subject), "middle": middle}
    out = ROOT / "still.mp4"
    ex.render_segment({"visual": visual, "frames": 2, "motion": motion}, out, ex.pick_encoder()[1])
    return frame_number(out, 0)


@check
def focus_centres_the_subject():
    """a focus on the white square's x puts it in the middle of the frame; through a 1.25 zoom, [x, top], the middle and [x, bottom] hold the window at the top, middle and bottom of the picture; near the edge the crop stops at the edge"""
    assets()
    on_marker = still_of(ex.parse_visual({"file": "shot.png", "focus": 1080 / 1920}, ROOT))
    left, right, _ = marker_box(on_marker)
    middle = (left + right) / 2
    # The square is at y 300-340 of 1080. A 1.25 zoom keeps 864 rows: 0-864 at the top, 108-972 in the middle and
    # 216-1080 at the bottom, so on screen (x 1920/864) the square spans 667-756, 427-516 and 187-276.
    spans = {}
    for name, down in (("top", "top"), ("middle", "middle"), ("bottom", 1.0)):
        rows = np.nonzero(still_of(ex.parse_visual({"file": "shot.png", "focus": [1080 / 1920, down]}, ROOT), 1.25)
                          .min(axis=2) > 225)[0]
        spans[name] = (int(rows.min()), int(rows.max()))
    edge = still_of(ex.parse_visual({"file": "shot.png", "focus": 0.02}, ROOT))
    expected = {"top": (667, 756), "middle": (427, 516), "bottom": (187, 276)}
    print(f"       square's middle at x {middle:.0f} (frame middle 540); rows at 1.25 zoom {spans}, expected {expected}")
    return (abs(middle - 540) <= 6 and all(abs(spans[k][i] - expected[k][i]) <= 8 for k in expected for i in (0, 1))
            and count_near(edge, BOX) > W * H * 0.5)


@check
def video_focus_moves_the_crop():
    """a video with focus 0 shows its left edge (a magenta bar there), focus 1 its right edge, and the centre neither"""
    assets()
    def first_frame(focus):
        v = ex.parse_visual({"file": "a.mp4", "focus": focus}, ROOT)
        out = ROOT / f"vfocus_{focus}.mp4"
        ex.render_segment({"visual": v, "frames": 3, "start_s": 0.0, "fill": "play"}, out, ex.pick_encoder()[1])
        return frame_number(out, 0)
    magenta = lambda pic: (count_near(pic[:, :W // 2], (255, 0, 255), 40), count_near(pic[:, W // 2:], (255, 0, 255), 40))
    l, c, r = magenta(first_frame(0)), magenta(first_frame(0.5)), magenta(first_frame(1))
    print(f"       magenta pixels (left half, right half): focus 0 {l}, 0.5 {c}, 1 {r}")
    return l[0] > W * H * 0.3 and l[1] == 0 and r[1] > W * H * 0.3 and r[0] == 0 and c == (0, 0)


def pan_positions(motion, frames):
    """Where the window's left edge is in the ramp picture on each frame, in its pixels, from the mean brightness."""
    v = ex.parse_visual({"file": "ramp.png", "pan": "right"}, ROOT)
    out = ROOT / f"pan_{motion['kind']}_{frames}.mp4"
    ex.render_segment({"visual": v, "frames": frames, "motion": motion}, out, ex.pick_encoder()[1])
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(out), "-vf", "scale=270:480:flags=area", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    rgb = np.frombuffer(raw, np.uint8).reshape(-1, 480, 270, 3).astype(float)
    # The window is 1080/1920 x 1080 = 607.5 ramp pixels wide, so its mean is the ramp's value at its left edge + 303.75.
    return rgb.mean(axis=(1, 2, 3)) * 1919 / 255 - 607.5 / 2, len(rgb)


@check
def pan_slides_evenly_edge_to_edge():
    """a pan right on a real render starts at the left edge, ends at the right edge, and moves the same distance every frame (no stall, no jump); a pan left does the reverse"""
    assets()
    v = ex.parse_visual({"file": "ramp.png", "pan": "right"}, ROOT)
    right, n = pan_positions(ex.make_reveal(v, "right"), 66)
    left, _ = pan_positions(ex.make_reveal(v, "left"), 66)
    steps = np.diff(right)
    # Brightness comes back a level or so darker after the trip through video, so positions read about 10 px low at
    # both ends alike; what must hold is the whole 1312.5 px of travel, the same ends both ways, and an even step.
    print(f"       pan right: {n} frames, from x {right[0]:.1f} to {right[-1]:.1f} ({right[-1] - right[0]:.1f} of the 1312.5 "
          f"px there is), step {steps.mean():.2f} px, spread {steps.min():.2f}-{steps.max():.2f}; pan left from "
          f"{left[0]:.1f} to {left[-1]:.1f}")
    return (n == 66 and abs(right[-1] - right[0] - 1312.5) < 3 and abs(right[0]) < 15 and steps.min() > 0
            and steps.max() - steps.min() < 1.0 and abs(left[0] - right[-1]) < 1 and abs(left[-1] - right[0]) < 1)


@check
def pan_opening_loops_back():
    """when the first shot is a pan, the loop ending slides back and its last frame is the Short's first"""
    assets()
    v = ex.parse_visual({"file": "shot.png", "pan": "right"}, ROOT)
    opening = ex.make_reveal(v, "right")
    first = still_frame(v, opening, 66, 0)
    tail = ex.return_motion(opening, 66, 45)
    last = still_frame(v, tail, 45, 44)
    diff = float(np.abs(first - last).mean())
    print(f"       mean difference between the first and the last frame: {diff:.2f} of 255")
    return tail.get("wide") and diff < 2.0


@check
def video_opening_loops_back():
    """when the first shot is a video with no footage before its start (a clip used from 0), the loop ending plays the clip backwards into its first frame, so the Short's last frame is its first and the picture never stands still (before PR 18 it held that first frame for 1.5 s): a 1 s clip full screen, which turns round to fill the 1.5 s, and a long clip framed; an opening later in a file plays the 1.5 s just before it, as since PR 15"""
    assets()
    results = []
    moved = lambda a, b: float((np.abs(a - b).max(axis=2) > 40).mean())
    for name, raw in (("full", {"file": "stops.mp4"}), ("framed", {"file": "b.mp4", "layout": "framed"}),
                      ("later", {"file": "b.mp4", "at": "3", "layout": "framed"})):
        v = settled(raw)
        assert not ex.place_visuals([{"visuals": [v]}], "word")[0]
        slots = [{"line": 0, "start": 0, "end": 30, "frames": 30, "visual": v},
                 {"line": 0, "start": 30, "end": 75, "frames": 45, "visual": v, "loop_tail": True}]
        ex.plan_videos(slots)
        ex.assign_backgrounds(slots)
        outs = [ROOT / f"vloop_{name}_{k}.mp4" for k in range(2)]
        for slot, out in zip(slots, outs):
            ex.render_segment(slot, out, ex.pick_encoder()[1])
        diff = float(np.abs(frame_number(outs[0], 0) - frame_number(outs[1], 44)).mean())
        tail = [frame_number(outs[1], k) for k in (0, 11, 22, 33, 44)]
        still = min(moved(a, b) for a, b in zip(tail, tail[1:]))      # the least any quarter of the ending moves
        length = int(probe(outs[1])["streams"][0]["nb_read_frames"])
        results.append((name, v["layout"], slots[1]["start_s"], bool(slots[1].get("bounce")), round(diff, 2),
                        round(still, 4), length))
    print(f"       (shot, layout, loop ending's start s, runs backwards, first vs last frame, share of the picture "
          f"that moves in its stillest quarter, frames) {results}")
    return (all(r[4] < 2.0 and r[5] > 0.0005 and r[6] == 45 for r in results)
            and [r[1] for r in results] == ["full", "framed", "framed"]
            and results[0][2:4] == (0.0, True) and results[1][2:4] == (0.0, True) and results[2][2:4] == (1.5, False)
            and ex.bounce_order(30, 45)[:2] == [14, 15] and ex.bounce_order(30, 45)[15:17] == [29, 28]
            and ex.bounce_order(30, 45)[-2:] == [1, 0] and ex.bounce_order(45, 45) == list(range(44, -1, -1)))


def still_frame(visual, motion, frames, n):
    out = ROOT / f"seg_{motion['kind']}_{frames}.mp4"
    ex.render_segment({"visual": visual, "frames": frames, "motion": motion}, out, ex.pick_encoder()[1])
    return frame_number(out, n)


@check
def weights_share_a_line():
    """weights 1, 2, 1 split a 4 s line 1 s, 2 s, 1 s, and no weight means an even split as before"""
    v = lambda name, w=1.0: {"source": name, "kind": "card", "text": name, **({"weight": w} if w != 1.0 else {})}
    weighted = ex.plan_slots([{"visuals": [v("a"), v("b", 2), v("c")]}, {"visuals": [v("d")]}], [0.0, 4.0], 240)
    even = ex.plan_slots([{"visuals": [v("a"), v("b"), v("c")]}, {"visuals": [v("d")]}], [0.0, 190 / FPS], 400)
    return ([s["frames"] for s in weighted[:3]] == [30, 60, 30] and [s["frames"] for s in even[:3]] == [63, 63, 64])


@check
def pacing_is_counted_and_warned():
    """shot count and average leave the loop ending out of the average; a 2.6 s still warns, a 3 s pan doesn't; a pan faster than 1.2 screen widths a second warns"""
    assets()
    card = {"source": "card:A", "kind": "card", "text": "A"}
    slots = [{"line": 0, "frames": 45, "visual": card}, {"line": 0, "frames": 78, "visual": card},
             {"line": 0, "frames": 45, "visual": card, "loop_tail": True}]
    stats = ex.shot_stats(slots)
    long_still = ex.slot_warnings([{"line": 0, "frames": 78, "visual": card}], [{}], 90)
    pan = ex.parse_visual({"file": "shot.png", "pan": "right"}, ROOT)
    slow = {"line": 0, "frames": 90, "visual": pan, "motion": ex.make_reveal(pan, "right")}
    fast = {"line": 0, "frames": 45, "visual": pan, "motion": ex.make_reveal(pan, "right")}
    print(f"       stats {stats}; pans: {ex.pan_speed(slow):.2f} and {ex.pan_speed(fast):.2f} screen widths a second")
    return (stats == {"count": 3, "own": 2, "average_s": 2.05, "longest_s": 2.6} and len(long_still) == 1
            and "2.6 s" in long_still[0] and not ex.slot_warnings([slow], [{}], 90)
            and any("screen widths" in w for w in ex.slot_warnings([fast], [{}], 90)))


@check
def crossed_out_card_draws_a_bar_over_its_text():
    """on real pixels: the red bar spans the crossed-out line's text with some to spare, sits across the middle of its capitals, misses the green line below, and arrives after the text"""
    folder = ROOT / "bar"
    folder.mkdir(exist_ok=True)
    events = ex.title_events(0, 0, 2, "~NOV 12~|*NOV 10*", ex.CARD_Y, ex.POP)
    (folder / "b.ass").write_text(ex.ass_document(events), encoding="utf-8")
    copy_fonts(folder)
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r=10:d=2",
                    "-vf", "ass=b.ass:fontsdir=.", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv444p",
                    "b.mp4"], cwd=folder, check=True, capture_output=True)
    early, late = frame_at(folder / "b.mp4", 0.1), frame_at(folder / "b.mp4", 1.0)
    red = (late[..., 0] > 190) & (late[..., 1] < 100) & (late[..., 2] < 100)
    white = late.min(axis=2) > 200
    green = (late[..., 1] > 190) & (late[..., 0] < 120)
    ry, rx = np.nonzero(red)
    wy, wx = np.nonzero(white)
    gy, _ = np.nonzero(green)
    caps = (wy.min(), wy.max())
    bar_middle = (ry.min() + ry.max()) / 2
    print(f"       text x {wx.min()}-{wx.max()} y {caps[0]}-{caps[1]}; bar x {rx.min()}-{rx.max()} y {ry.min()}-{ry.max()}; "
          f"green from y {gy.min()}; red pixels at 0.1 s {int(((early[..., 0] > 190) & (early[..., 1] < 100)).sum())}")
    return (rx.min() < wx.min() and rx.max() > wx.max() and caps[0] + 0.3 * (caps[1] - caps[0]) < bar_middle
            < caps[1] - 0.3 * (caps[1] - caps[0]) and ry.max() < gy.min() and wy.max() < gy.min()
            and int(((early[..., 0] > 190) & (early[..., 1] < 100)).sum()) == 0)


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
                          music=None, **OLD_LOOK, lines=[
        {"say": "The first line has two parts, and 212 gigabytes is one of them.", "visuals": ["a.mp4@0:03", "shot.png"],
         "green": ["212 GIGABYTES"], "red": []},
        {"say": "Danger is the word you should remember today. It comes up right here.", "visuals": ["b.mp4@1.5"], "green": [], "red": ["DANGER"]},
        {"say": "A map of the state comes last, so watch how the ending works.", "visuals": ["shot.png", "card:THE END"]}])
    result = run_tool(script)
    assert result.returncode == 0, result.stderr[-500:]
    folder = Path(re.search(r"Run folder: (.+)", result.stdout).group(1).strip())
    return folder, json.loads((folder / "log.json").read_text(encoding="utf-8")), result.stdout


def plain(line):
    """An .ass line without its {style commands}, so its words read in a row."""
    return re.sub(r"\{[^}]*\}", "", line)


def caption_starts(folder, needle=None):
    """The start time, in seconds, of each caption event, or just those holding the text."""
    out = []
    for line in (folder / "captions.ass").read_text(encoding="utf-8").splitlines():
        m = re.match(r"Dialogue: 2,(\d+):(\d+):(\d+\.\d+),", line)
        if m and (needle is None or needle in plain(line)):
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
    """the .ass has 212 GIGABYTES in cyan (a green tag since PR 17) and DANGER in red, and untagged words have no colour of their own"""
    text = (main_run()[0] / "captions.ass").read_text(encoding="utf-8")
    white_line = next(line for line in text.splitlines() if "THE FIRST" in plain(line))
    cyan, red = ex.COLOURS["green"], ex.COLOURS["red"]
    return (cyan == "&HFFE500&" and f"{{\\c{cyan}}}212" in text and f"{{\\c{cyan}}}GIGABYTES" in text
            and f"{{\\c{red}}}DANGER" in text and cyan not in white_line and red not in white_line and "Anton" in text)


@check
def colours_really_show_on_screen():
    """while a tagged chunk shows, the caption band has that exact cyan or red; while an untagged one shows, neither"""
    folder = main_run()[0]
    short = folder / "short.mp4"
    band = lambda seconds: frame_at(short, seconds + 0.25)[1100:1540]
    green = band(caption_starts(folder, "GIGABYTES")[0])
    red = band(caption_starts(folder, "DANGER")[0])
    plain = band(caption_starts(folder, "THE FIRST")[0])
    counts = count_near(green, CYAN), count_near(red, RED), count_near(plain, CYAN), count_near(plain, RED), count_near(green, RED)
    print(f"       cyan px {counts[0]}, red px {counts[1]}; untagged chunk: cyan {counts[2]}, red {counts[3]}")
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


@check
def voice_has_no_slow_drift():
    """Kokoro's slow swell under 20 Hz (straight from the model it held about a third of the voice's energy) is gone from voice.wav, so it can't use up the limiter's room"""
    samples, rate = voice_samples(main_run()[0])
    power = np.abs(np.fft.rfft(samples)) ** 2
    below = 10 * np.log10(power[np.fft.rfftfreq(len(samples), 1 / rate) < 20].sum() / power.sum())
    print(f"       under 20 Hz: {below:.1f} dB of the voice's energy; mean sample {samples.mean():+.4f}")
    return below < -30 and abs(samples.mean()) < 0.002


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
    """the log has the durations and target, the shot count, a visual for every second, each image's move, and the speed-over-1.1 warning; the console prints the runtime and the shots"""
    folder, log, stdout = main_run()
    images = [v for v in log["visuals"] if v["kind"] == "image"]
    return (len(log["per_second"]) == math.ceil(log["durations"]["frames"] / FPS) and log["per_second"][0]["visual"] == "a.mp4@0:03"
            and all(v["motion"] for v in images) and log["voice"] == "am_puck" and log["speed"] == 1.18
            and any("faster than 1.1" in w for w in log["warnings"]) and "WARNING" in stdout
            and log["durations"]["target_s"] == [34, 39] and log["shots"]["count"] == len(log["visuals"])
            and re.search(r"Runtime \d+\.\d s \(target 34-39 s", stdout)
            and re.search(rf"Shots: {len(log['visuals'])} \({len(log['visuals']) - 1} plus the 1.5 s loop ending\), average", stdout)
            and {"voice_lufs", "final_lufs"} <= set(log["loudness"]))


@functools.cache
def pacing_run():
    """A script with this PR's shots: a pan to open (so the loop ending slides back), a crossed-out card, a weighted
    video with a focus, and an image with [across, down]."""
    assets()
    script = write_script("pacing.json", title="Pacing test", **OLD_LOOK, lines=[
        {"say": "This line opens on a slow pan and then shows a card with a date crossed out.",
         "visuals": [{"file": "ramp.png", "pan": "right", "weight": 2}, {"card": "~NOV 12~|*NOV 10*"}]},
        {"say": "Then a clip framed on its left edge, and a photo held near its top.",
         "visuals": [{"file": "a.mp4", "at": "0:01", "focus": 0, "weight": 0.8}, {"file": "shot.png", "focus": [0.55, "top"]}]}])
    result = run_tool(script)
    assert result.returncode == 0, result.stderr[-500:]
    folder = Path(re.search(r"Run folder: (.+)", result.stdout).group(1).strip())
    return folder, json.loads((folder / "log.json").read_text(encoding="utf-8")), result.stdout


@check
def pacing_render_joins_and_loops():
    """a real render with a pan, a crossed-out card, a focused clip and weights: exact frame count, the pan's speed and focus in the log, the bar on screen, the clip's left edge shown, and the last frame back on the first"""
    folder, log, stdout = pacing_run()
    short = folder / "short.mp4"
    info = probe(short)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    by_source = {x["source"]: x for x in log["visuals"]}
    card = by_source["card:~NOV 12~|*NOV 10*"]
    clip = next(x for x in log["visuals"] if x["source"].startswith("a.mp4"))
    bar = frame_at(short, card["start_s"] + card["seconds"] - 0.2)
    red = int(((bar[..., 0] > 190) & (bar[..., 1] < 100) & (bar[..., 2] < 100)).sum())
    clip_frame = frame_at(short, clip["start_s"] + 0.1)
    magenta_left = count_near(clip_frame[:, :W // 2], (255, 0, 255), 40)
    first, last = frame_number(short, 0)[:TOP_BAND], frame_number(short, int(v["nb_read_frames"]) - 1)[:TOP_BAND]
    seam = float(np.abs(first - last).mean())
    pan = log["visuals"][0]
    print(f"       {v['nb_read_frames']} frames (log {log['durations']['frames']}); pan {pan['pan_widths_per_s']} widths/s; "
          f"red bar px {red}; magenta px in the clip's left half {magenta_left}; first vs last frame {seam:.2f}")
    return (int(v["nb_read_frames"]) == log["durations"]["frames"] and pan["pan"] == "right" and pan["pan_widths_per_s"] > 0
            and pan["weight"] == 2 and clip["focus"] == [0.0, 0.5] and log["visuals"][-1]["loop_tail"]
            and log["visuals"][-1]["motion"] == "back_to_first_frame" and red > 3000 and magenta_left > W * H * 0.2
            and seam < 2.0 and "Shots:" in stdout)


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


YELLOW = (255, 230, 0)


def ass_seconds(text):
    h, m, s = text.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


@check
def spoken_word_is_highlighted():
    """in the .ass every untagged word turns yellow at its own start (from the log's word times, to the ms) and back when the next starts, tagged words keep their colour; the three words said while the last line's card is up (since PR 19 that card is shown, not dropped for the loop ending) come back the moment the card leaves, already lit; on screen the yellow moves from word to word inside one chunk"""
    folder, log, _ = main_run()
    words = log["word_times"]
    hidden = log["look"]["captions_hidden_over_cards_s"]
    came_back = []  # the words said under a card, whose chunk is shown again when the card leaves
    found = []  # (start of the event, the word's tags, the word)
    for line in (folder / "captions.ass").read_text(encoding="utf-8").splitlines():
        m = re.match(r"Dialogue: 2,([\d:.]+),", line)
        if m:
            found += [(ass_seconds(m.group(1)), tags, word)
                      for tags, word in re.findall(r"\{([^}]*)\}([^{}\s\\]+)\{\\c&HFFFFFF&\}", line)]
    late, untagged = [], 0
    for (start, tags, word), timed in zip(found, words):
        if ex.HIGHLIGHT not in tags:
            continue
        untagged += 1
        on = 0 if tags.startswith(f"\\c{ex.HIGHLIGHT}") else int(re.match(r"\\t\((\d+),", tags).group(1))
        if any(a <= timed["start_s"] < b and abs(start - b) <= 0.011 for a, b in hidden):
            came_back.append(word)
            if word != timed["text"] or on != 0:
                late.append((word, on))
        elif word != timed["text"] or abs(on - round((timed["start_s"] - start) * 1000)) > 1:
            late.append((word, on))
    tagged_plain = all(ex.HIGHLIGHT not in t for _, t, w in found if w in ("212", "GIGABYTES", "DANGER"))
    # On screen: the first chunk with three untagged words, a moment into its second and third word.
    chunk = next(i for i in range(len(found) - 2) if found[i][0] == found[i + 1][0] == found[i + 2][0]
                 and all(ex.HIGHLIGHT in found[i + k][1] for k in range(3)))
    short = folder / "short.mp4"
    lit = [count_near(frame_at(short, words[chunk + k]["start_s"] + 0.06)[1100:1540], YELLOW, 40) for k in (1, 2)]
    masks = [np.abs(frame_at(short, words[chunk + k]["start_s"] + 0.06)[1100:1540] - np.array(YELLOW)).max(axis=2) <= 40
             for k in (1, 2)]
    overlap = int((masks[0] & masks[1]).sum())
    print(f"       {untagged} untagged words, {len(late)} off their time {late[:5]}; back when the card leaves ({hidden}): "
          f"{came_back}; on screen: yellow px {lit} for "
          f"{words[chunk + 1]['text']} then {words[chunk + 2]['text']}, {overlap} px yellow in both")
    return (len(found) == len(words) and untagged >= 15 and not late and tagged_plain and min(lit) > 1500
            and len(hidden) == 1 and came_back == ["STATE", "COMES", "LAST"]
            and overlap < min(lit) * 0.1)


@functools.cache
def sound_run():
    """Music picked by "auto" from the music folder, and sound effects: a card in line 1, a pan in line 2."""
    music_folder()
    script = write_script("sound_fx.json", title="Sound test", music="auto", lines=[
        {"say": "This first line has a card in the middle of it, which lands with a hit.", "visuals": ["b.mp4@1", "card:BIG NEWS"]},
        {"say": "The second line starts with a whoosh, and then a slow pan gets one of its own.",
         "visuals": ["shot.png", {"file": "ramp.png", "pan": "right", "weight": 2}]}])
    result = run_tool(script)
    assert result.returncode == 0, result.stderr[-500:]
    folder = Path(re.search(r"Run folder: (.+)", result.stdout).group(1).strip())
    return folder, json.loads((folder / "log.json").read_text(encoding="utf-8")), result.stdout


@check
def music_and_sound_effects_render():
    """"auto" picks the long track, the music fades in, a hit lands on the card and whooshes on line 2 and its pan (as in the log and the console), the hit really is in the sound, and the mix is -14 LUFS with peaks under -1 dB"""
    folder, log, stdout = sound_run()
    short = folder / "short.mp4"
    visuals = log["visuals"]
    card, line2, pan = (next(v for v in visuals if v["source"] == "card:BIG NEWS"), next(v for v in visuals if v["line"] == 2),
                        next(v for v in visuals if v.get("pan")))
    want = [("hit", card["start_s"]), ("whoosh", line2["start_s"]), ("whoosh", pan["start_s"])]
    got = [(e["kind"], e["at_s"]) for e in log["sfx"]]
    start, settled = band_level(short, MUSIC_HZ, 0.0, 0.1), band_level(short, MUSIC_HZ, 0.6, 1.0)
    hit_band, voice_band = low_level(short, card["start_s"], 0.3), low_level(folder / "voice.wav", card["start_s"], 0.3)
    loud = log["loudness"]
    print(f"       music {Path(log['music']['file']).name} ({log['music']['chosen']}); sfx {got}; music band "
          f"{start:.1f} dB in the first 0.1 s, {settled:.1f} dB after; under 70 Hz at the hit {hit_band:.1f} dB, "
          f"{voice_band:.1f} dB in the voice alone; {loud['final_lufs']} LUFS, peak {loud['final_true_peak_db']} dB")
    return (Path(log["music"]["file"]).name == "long.wav" and "automatically" in log["music"]["chosen"]
            and all(k == wk and abs(a - wa) < 0.002 for (k, a), (wk, wa) in zip(got, want)) and len(got) == 3
            and start < settled - 6 and hit_band > voice_band + 10 and abs(loud["final_lufs"] + 14) <= 1
            and loud["final_true_peak_db"] <= -1.0 and "Music: long.wav (picked automatically" in stdout
            and "Sound effects: 2 whooshes, 1 hits" in stdout and "Loudness: -1" in stdout)


@check
def auto_with_no_music_folder_still_renders():
    """"auto" with no music folder renders without music and says so; sfx false means no sound effects; the voice is the mix's level"""
    assets()
    root = ROOT / "no_music_root"
    root.mkdir(exist_ok=True)
    shutil.copy(ROOT / "shot.png", root)
    script = write_script("no_music.json", title="No music", music="auto", sfx=False,
                          lines=[{"say": "There is no music folder here at all.", "visuals": ["shot.png"]}])
    result = subprocess.run([sys.executable, str(HERE / "explainer.py"), str(script), "--root", str(root), "--out", str(OUT)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        print(f"       {result.stderr[-300:]}")
        return False
    folder = Path(re.search(r"Run folder: (.+)", result.stdout).group(1).strip())
    log = json.loads((folder / "log.json").read_text(encoding="utf-8"))
    return (log["music"]["file"] is None and log["sfx"] == [] and any("has no audio files" in w for w in log["warnings"])
            and "Music: none" in result.stdout and "Sound effects: off" in result.stdout
            and band_level(folder / "short.mp4", MUSIC_HZ) < -70 and abs(log["loudness"]["final_lufs"] + 14) <= 1)


# --- PR 17: the look ---

def look_shot(**fields):
    return {"file": "shot.png", **fields}


def settled(raw, default=None):
    """A shot read and given its layout, as load_script does."""
    visual = ex.parse_visual(raw, ROOT)
    ex.resolve_layout(visual, default)
    return visual


@check
def new_fields_are_read():
    """layout, card_aspect (which means framed), a label (its text alone, or text, style and pos), marks and a card's bg; the script's layout, caption_style and grade; with none set: framed for a landscape picture, full for a portrait one, a square card, word captions, grade on"""
    assets()
    a = settled(look_shot(layout="full"))
    b = settled(look_shot(card_aspect="4:5", label="gta v  |  2013"))
    c = settled(look_shot(label={"text": "no way in", "style": "no", "pos": "bottom"},
                          marks=[{"icon": "circle", "x": 0.55, "y": 0.3, "at": 0.5}, {"x": 0.5, "y": 0.5}]))
    d = ex.parse_visual({"card": "212 GB", "bg": "b.mp4@2", "label": {"text": "PS5", "style": "number"}}, ROOT)
    tall, forced = settled("tall.png"), settled("tall.png", "framed")
    aspect_wins = settled({"file": "tall.png", "card_aspect": "16:9"}, "full")
    script = ex.load_script(write_script("look_defaults.json", **one_line()), ROOT)
    pinned = ex.load_script(write_script("look_pinned.json", **one_line(**OLD_LOOK)), ROOT)
    shot = script["lines"][0]["visuals"][0]
    return (a["layout"] == "full" and b["layout"] == "framed" and b["aspect"] == (4, 5)
            and b["label"] == {"text": "GTA V | 2013", "style": "info", "pos": "top"}
            and c["layout"] == "framed" and c["card_aspect"] == "1:1" and c["label"]["style"] == "no"
            and c["label"]["pos"] == "bottom"
            and c["marks"] == [{"icon": "circle", "x": 0.55, "y": 0.3, "at": 0.5},
                               {"icon": "x", "x": 0.5, "y": 0.5, "at": ex.MARK_EARLIEST_S}]
            and d["bg"]["kind"] == "video" and d["bg"]["start"] == 2 and d["label"]["style"] == "number"
            and tall["layout"] == "full" and forced["layout"] == "framed" and aspect_wins["layout"] == "framed"
            and script["caption_style"] == "word" and script["grade"] is True and script["layout"] is None
            and shot["layout"] == "framed" and shot["card_aspect"] == "1:1" and shot["frame"]["outer"] == (60, 510, 900, 900)
            and pinned["caption_style"] == "phrase" and pinned["grade"] is False
            and pinned["lines"][0]["visuals"][0]["layout"] == "full")


@check
def bad_new_fields_stop():
    """a bad layout or card_aspect (or one on a full-screen shot), label, mark or bg, a pan that a 16:9 card leaves no room for, a label too wide to read, a mark outside what the shot shows, and bad look settings for the script: all listed, exit 2, nothing written"""
    assets()
    out = ROOT / "bad_look_out"
    bad = [look_shot(layout="wide"), look_shot(card_aspect="3:2"), look_shot(layout="full", card_aspect="1:1"),
           look_shot(label={"text": "X", "style": "pink"}), look_shot(label={"text": "X", "pos": "middle"}),
           look_shot(label={"text": " "}), look_shot(label={"text": "X", "size": 3}),
           look_shot(marks=[{"icon": "star", "x": 0.5, "y": 0.5}]), look_shot(marks=[{"x": 1.5, "y": 0.5}]),
           look_shot(marks=[{"x": 0.5, "y": 0.5, "at": -1}]), look_shot(marks=[]), look_shot(bg="b.mp4"),
           {"card": "X", "bg": "card:Y"}, {"file": "shot.png", "pan": "right", "card_aspect": "16:9"}]
    first = run_tool(write_script("bad_look.json", title="Bad look", lines=[{"say": "One two three.", "visuals": bad}]), out)
    second = run_tool(write_script("bad_look2.json", title="Bad look", layout="stretched", caption_style="karaoke",
                                   grade="yes", lines=[{"say": "One two three.", "visuals": ["shot.png"]}]), out)
    third = run_tool(write_script("bad_look3.json", title="Bad look", lines=[{"say": "One two three.", "visuals": [
        look_shot(label="THIS LABEL IS FAR TOO LONG TO EVER FIT"), look_shot(marks=[{"x": 0.02, "y": 0.5}]),
        look_shot(pan="right", marks=[{"x": 0.5, "y": 0.5}])]}]), out)
    wanted = ["layout is framed", "card_aspect is 1:1, 4:5, 16:9", "this shot fills the screen", "label style is info",
              "label pos is top or bottom", "a label needs its text", "not 'size'", "a mark's icon is",
              "a mark's x and y", "a mark's at", "marks is a list", "bg is for cards", "bg is the picture behind the card",
              "already shows its whole width"]
    missing = [w for w in wanted if w not in first.stderr]
    missing += [w for w in ("layout is framed (pictures", "caption_style is word", "grade is true") if w not in second.stderr]
    missing += [w for w in ("too wide to read", "outside the part of the picture", "can't pan") if w not in third.stderr]
    if missing:
        print(f"       missing from the errors: {missing}")
    return (first.returncode == second.returncode == third.returncode == 2 and not missing
            and first.stderr.count("line 1:") >= len(bad) and not out.exists())


def placed(caption_style, **fields):
    visual = settled(look_shot(**fields))
    problems, warnings = ex.place_visuals([{"visuals": [visual]}], caption_style)
    assert not problems, problems
    return visual, warnings


def label_clear(visual, caption_style):
    up, down = ex.label_reach(visual["label"])
    lo, hi = ex.CAPTION_BANDS[caption_style]
    top, bottom = visual["label"]["y"] - up, visual["label"]["y"] + down
    return (bottom + ex.LABEL_GAP <= lo or top - ex.LABEL_GAP >= hi) and top >= 380 and bottom <= 1540


@check
def cards_and_labels_find_their_place():
    """a square card is 900 px wide at x 60-960 in the middle of the safe area, with an 880 px picture inside; a label sits on its edge and clears the captions; a 16:9 card with a bottom label moves up to clear the word captions; with phrase captions a square card's bottom label goes to the top edge and says so; a full-screen bottom label clears the word captions"""
    assets()
    plain_card, _ = placed("word")
    square, w1 = placed("word", label={"text": "PS5", "pos": "bottom"})
    on_top, _ = placed("word", label="PS5")
    wide, w2 = placed("word", card_aspect="16:9", label={"text": "PS5", "pos": "bottom"})
    moved, w3 = placed("phrase", label={"text": "PS5", "pos": "bottom"})
    full, w4 = placed("word", layout="full", label={"text": "PS5", "pos": "bottom"})
    ox, oy, ow, oh = wide["frame"]["outer"]
    print(f"       square {plain_card['frame']}; with a bottom label {square['frame']['outer']}, label y "
          f"{square['label']['y']}; 16:9 {wide['frame']['outer']}, label y {wide['label']['y']}; phrase: {w3}")
    return (plain_card["frame"] == {"outer": (60, 510, 900, 900), "content": (70, 520, 880, 880)}
            and square["label"]["y"] == square["frame"]["outer"][1] + 900 and label_clear(square, "word") and not w1
            and on_top["label"]["y"] == on_top["frame"]["outer"][1] and label_clear(on_top, "word")
            and (ow, oh) == (900, 506) and wide["label"]["y"] == oy + oh and oy + oh / 2 < 960
            and label_clear(wide, "word") and not w2
            and moved["label"]["pos"] == "top" and any("goes on the top edge" in w for w in w3)
            and label_clear(moved, "phrase") and full["label"]["y"] == ex.FULL_LABEL_Y["bottom"]
            and label_clear(full, "word") and not w4)


def render_ass(events, times, name, size=(W, H)):
    """The events drawn over mid grey, one frame at each of the times, as RGB arrays."""
    folder = ROOT / name
    folder.mkdir(exist_ok=True)
    (folder / "t.ass").write_text(ex.ass_document(events), encoding="utf-8")
    copy_fonts(folder)
    frames = []
    for t in times:
        raw = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"color=0x808080:s={size[0]}x{size[1]}:r=100:d={t + 0.02}",
                              "-vf", f"ass=t.ass:fontsdir=.,select='gte(t\\,{t:.3f})'", "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", "rgb24", "-"], cwd=folder, capture_output=True, check=True).stdout
        frames.append(np.frombuffer(raw, np.uint8).reshape(size[1], size[0], 3).astype(np.int32))
    return frames


def box_of(mask):
    ys, xs = np.nonzero(mask)
    return (int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())) if len(xs) else None


@check
def word_captions_follow_each_word():
    """one event a word from its own start, yellow unless tagged (cyan for green, red for red), gone when the next word starts or a moment after a pause, none while a card is up (a word that outlasts the card comes back); on real pixels each word's capitals sit on WORD_Y and a word too long for the screen is drawn smaller to stay in the safe area"""
    tag = ("green", "212 GIGS", 1)
    words = [fake_word("THE", start=0.0, end=0.2), fake_word("212", colour="green", tag=tag, start=0.2, end=0.5),
             fake_word("GIGS", colour="green", tag=tag, pause=True, start=0.5, end=0.9),
             fake_word("DANGER", 1, colour="red", tag=("red", "DANGER", 3), start=1.4, end=1.8),
             fake_word("ABSOLUTELY-UNBELIEVABLE", 1, start=1.8, end=2.5)]
    events = ex.word_events(words, 3.0, hidden=[(2.0, 2.3)])
    spans = [(ass_seconds(m.group(1)), ass_seconds(m.group(2)), plain(e).split(",,")[-1])   # the word, after the fields
             for e in events for m in [re.match(r"Dialogue: 2,([\d:.]+),([\d:.]+),", e)]]
    colours = [re.search(r"\\c(&H[0-9A-F]+&)", e).group(1) for e in events]
    sizes = [int(re.search(r"\\fs(\d+)", e).group(1)) for e in events]
    want = [(0.0, 0.2, "THE"), (0.2, 0.5, "212"), (0.5, 1.15, "GIGS"), (1.4, 1.8, "DANGER"),
            (1.8, 2.0, "ABSOLUTELY-UNBELIEVABLE"), (2.3, 2.75, "ABSOLUTELY-UNBELIEVABLE")]
    timing = all(abs(a - wa) < 0.006 and abs(b - wb) < 0.006 and t == wt for (a, b, t), (wa, wb, wt) in zip(spans, want))
    painted = colours[:4] == [ex.HIGHLIGHT, ex.COLOURS["green"], ex.COLOURS["green"], ex.COLOURS["red"]]
    # Real pixels: each word alone, one a second, half a second in (after the pop).
    alone = ex.word_events([fake_word(w, start=i, end=i + 0.8) for i, w in enumerate(["MIDNIGHT", "WWWWWWWWWWWWWWWW", "6"])], 3.0)
    frames = render_ass(alone, [0.5, 1.5, 2.5], "words")
    yellow = [box_of(np.abs(f - np.array(YELLOW)).max(axis=2) <= 40) for f in frames]
    ink = [box_of(np.abs(f - 128).max(axis=2) > 12) for f in frames]
    middles = [(b[2] + b[3]) / 2 for b in yellow]
    print(f"       spans {[(round(a, 2), round(b, 2), t[:8]) for a, b, t in spans]}; sizes {sizes}; yellow boxes {yellow}; "
          f"ink with outline {ink}")
    return (len(events) == 6 and timing and painted and sizes[0] == ex.WORD_SIZE and sizes[4] < ex.WORD_SIZE
            and all(abs(m - ex.WORD_Y) <= 4 for m in middles)
            and all(b[0] >= SAFE[0] and b[1] <= SAFE[1] and b[2] >= ex.CAPTION_BANDS["word"][0]
                    and b[3] <= ex.CAPTION_BANDS["word"][1] for b in ink))


@check
def phrase_captions_step_aside_for_cards():
    """with phrase captions, a chunk that runs into a text card stops when the card comes and, if it outlasts it, comes back with its highlight still timed to the words (the last chunk still stays to the end, as in PR 16)"""
    words = [fake_word("ONE", start=0.0, end=0.3), fake_word("TWO", start=0.3, end=0.6), fake_word("THREE", start=1.2, end=1.5)]
    events = ex.caption_events(words, 2.0, hidden=[(0.2, 0.9)])
    spans = [(ass_seconds(m.group(1)), ass_seconds(m.group(2))) for e in events for m in [re.match(r"Dialogue: 2,([\d:.]+),([\d:.]+),", e)]]
    back = events[1]
    print(f"       spans {spans}; after the card: {back[back.index(',,') + 2:][:120]}")
    return (spans == [(0.0, 0.2), (0.9, 2.0)] and "\\c&H00E6FF&" in back.split("THREE")[0]
            and ex.visible(0, 1, [(0.2, 0.4), (0.5, 0.6)]) == [(0, 0.2), (0.4, 0.5), (0.6, 1)])


PINK = (255, 46, 147)


@check
def numbers_count_up_and_hold_their_place():
    """a card starting with 212 counts 0 to 212 over 0.6 s, a value a frame, easing out; on real pixels the number's right edge and the unit after it never move; text without a number at its start, or with it crossed out, doesn't count"""
    events = ex.title_events(0, 0, 2, "212 GB", ex.CARD_Y, ex.POP, count=True)
    values = [int(plain(e).split(",,")[-1]) for e in events[1:]]
    starts = [ass_seconds(re.match(r"Dialogue: 0,([\d:.]+),", e).group(1)) for e in events[1:]]
    # Two moments mid-count (after the 130 ms pop of the unit) and one long after. The unit's white is looked for right
    # of the number and its own white outline, which changes with the value.
    frames = render_ass(events, [0.15, 0.3, 1.0], "count")
    pink = [box_of(np.abs(f - np.array(PINK)).max(axis=2) <= 40) for f in frames]
    unit_from = pink[-1][1] + 40
    white = [box_of(f[:, unit_from:].min(axis=2) > 235) for f in frames]
    plainer = ex.title_events(0, 0, 2, "*NOV 10*|12 AM", ex.CARD_Y, ex.POP, count=True)
    struck = ex.title_events(0, 0, 2, "~212~ GB", ex.CARD_Y, ex.POP, count=True)
    print(f"       values {values[:5]}...{values[-3:]} ({len(values)}); pink (number) boxes {pink}; white (GB) boxes {white}")
    return (values[0] == 0 and values[-1] == 212 and all(a <= b for a, b in zip(values, values[1:])) and len(values) == 19
            and values[9] > 212 / 2 and all(abs(s - k / FPS) < 0.006 for k, s in enumerate(starts))
            and all(abs(p[1] - pink[-1][1]) <= 3 for p in pink) and pink[0][1] - pink[0][0] < pink[-1][1] - pink[-1][0]
            and all(w == white[-1] for w in white)
            and len(plainer) == 1 and len(struck) == 2)


@check
def labels_are_pills_on_the_card_edge():
    """on real pixels: a label is a pill of its colour (cyan info, red no, pink number) with white text inside, centred across on CENTRE_X and on its edge, at least as big as the C2 sample's; halfway through the pop the text is still in the pill's middle"""
    labels = [{"text": "GTA V | 2013", "style": "info", "pos": "top", "y": 600},
              {"text": "NO WAY IN", "style": "no", "pos": "top", "y": 1000},
              {"text": "212 GB", "style": "number", "pos": "top", "y": 1400}]
    ex.measure_labels(labels)
    events = [e for label in labels for e in ex.label_events(label, 0, 2)]
    late, early = render_ass(events, [1.0, 0.07], "pills")
    ok, notes = True, []
    for label, colour in zip(labels, (CYAN, RED, PINK)):
        y = label["y"]
        for frame, when in ((late, "settled"), (early, "popping")):
            band = frame[y - 80:y + 80]
            pill = box_of(np.abs(band - np.array(colour)).max(axis=2) <= 40)
            text = box_of(band.min(axis=2) > 235)
            inner = band[:, pill[0]:pill[1]] if pill else band
            centre = ((pill[0] + pill[1]) / 2, (pill[2] + pill[3]) / 2 + y - 80)
            words = box_of(inner.min(axis=2) > 235)
            words_centre = (pill[0] + (words[0] + words[1]) / 2, (words[2] + words[3]) / 2 + y - 80) if words else (0, 0)
            notes.append(f"{label['text']} {when}: pill {pill} text centre {words_centre[0]:.0f},{words_centre[1]:.0f}")
            ok &= (abs(centre[0] - ex.CENTRE_X) <= 3 and abs(centre[1] - y) <= 3 and abs(words_centre[0] - centre[0]) <= 4
                   and abs(words_centre[1] - centre[1]) <= 5)
            if when == "settled":
                ok &= label["size"][1] >= 95 and text is not None
    print("       " + "; ".join(notes))
    return ok


@check
def marks_land_on_their_spot():
    """a mark's spot on the picture goes through the shot's crop to the screen (framed and full), and on real pixels the X's red disc and the arrow's tip land there"""
    assets()
    framed = settled(look_shot(marks=[{"x": 0.5, "y": 0.3}, {"icon": "arrow", "x": 0.35, "y": 0.6}]))
    full = settled(look_shot(layout="full", marks=[{"x": 0.5, "y": 0.3}]))
    for v in (framed, full):
        assert not ex.place_visuals([{"visuals": [v]}], "word")[0]
    # Framed: the square crop is x 420-1500 of the 1920 x 1080 photo, drawn at 880 px from (70, 520).
    want_x = (70 + (960 - 420) / 1080 * 880, 520 + 0.3 * 880)
    want_arrow = (70 + (672 - 420) / 1080 * 880, 520 + 0.6 * 880)
    want_full = ((960 - 656) / 608 * W, 0.3 * H)    # the 9:16 crop is 608 px wide from x 656
    frames = render_ass(ex.mark_events(framed["marks"], 0, 2), [1.0], "marks")
    red = (frames[0][..., 0] > 200) & (frames[0][..., 1] < 90) & (frames[0][..., 2] < 90)
    ys, xs = np.nonzero(red[:, :400])  # the arrow, left of the X
    disc = box_of(red[:, 400:])
    centre = (400 + (disc[0] + disc[1]) / 2, (disc[2] + disc[3]) / 2)
    tip = (float(xs[ys == ys.max()].mean()), float(ys.max()))
    got = [m["screen"] for m in framed["marks"]] + [full["marks"][0]["screen"]]
    print(f"       screen spots {[(round(x), round(y)) for x, y in got]}, wanted {[(round(x), round(y)) for x, y in (want_x, want_arrow, want_full)]}; "
          f"X disc centre {centre[0]:.0f},{centre[1]:.0f}; arrow tip {tip[0]:.0f},{tip[1]:.0f}")
    return (all(abs(a - b) < 1 for p, q in zip(got, (want_x, want_arrow, want_full)) for a, b in zip(p, q))
            and abs(centre[0] - want_x[0]) <= 3 and abs(centre[1] - want_x[1]) <= 3
            and abs(tip[0] - want_arrow[0]) <= 4 and abs(tip[1] - want_arrow[1]) <= 6)


@check
def grade_adds_contrast_and_a_vignette():
    """the same full-screen shot rendered with the grade and without: with it, more contrast and colour in the middle and darker corners; without it, exactly the old picture"""
    assets()
    visual = ex.parse_visual("shot.png", ROOT)
    motion = {"kind": "zoom_in", "speed": 1.0, "zoom": (1.0, 1.0), "centre": (0.5, 0.5)}
    frames = {}
    for grade in (False, True):
        out = ROOT / f"grade_{grade}.mp4"
        ex.render_segment({"visual": visual, "frames": 2, "motion": motion, "grade": grade}, out, ex.pick_encoder()[1])
        frames[grade] = frame_number(out, 0)
    plain, graded = frames[False], frames[True]
    corner = lambda f: f[:200, :200].mean()
    middle = lambda f: f[860:1060, 440:640].astype(float)
    spread = lambda f: f.max(axis=2) - f.min(axis=2)
    print(f"       corner {corner(plain):.0f} -> {corner(graded):.0f}; middle colour spread {spread(middle(plain)).mean():.0f} -> "
          f"{spread(middle(graded)).mean():.0f}")
    return corner(graded) < corner(plain) - 8 and spread(middle(graded)).mean() > spread(middle(plain)).mean() + 3


@functools.cache
def look_run():
    """A script in the PR 17 look with nothing pinned: a framed photo with a label and marks, a counting number card
    over the next shot's picture, a framed video whose card grows in, a portrait picture full screen, a crossed-out
    card and a closing photo."""
    assets()
    script = write_script("look.json", title="Look test", lines=[
        {"say": "This first picture sits in a card, and then 212 gigabytes counts up over the next one.",
         "visuals": [{"file": "shot.png", "label": "GTA V | 2013", "marks": [{"icon": "x", "x": 0.4, "y": 0.62, "at": 0.4}]},
                     {"card": "212 GB", "label": {"text": "PS5", "style": "number"}},
                     {"file": "b.mp4", "at": "1", "label": {"text": "No way in", "style": "no", "pos": "bottom"}, "weight": 1.5}],
         "green": ["212 GIGABYTES"]},
        {"say": "A tall picture fills the whole screen here, a crossed out date comes after it, and then the first photo "
                "closes the test.",
         "visuals": [{"file": "tall.png", "label": "Tall"}, {"card": "~NOV 12~|*NOV 10*"}, "shot.png"]}])
    result = run_tool(script)
    assert result.returncode == 0, result.stderr[-800:]
    folder = Path(re.search(r"Run folder: (.+)", result.stdout).group(1).strip())
    return folder, json.loads((folder / "log.json").read_text(encoding="utf-8")), result.stdout


def shot_of(log, start):
    return next(v for v in log["visuals"] if v["source"].startswith(start))


@check
def look_render_has_no_black_screen():
    """a real render in the new look: every quarter second has a picture (never black or flat), the number card shows the next shot's picture blurred behind it, a portrait picture fills the screen, and the log and console say which layout each shot took"""
    folder, log, stdout = look_run()
    short = folder / "short.mp4"
    times = [t / 4 for t in range(int(log["durations"]["video_s"] * 4))]
    frames = [frame_at(short, t)[::8, ::8] for t in times]
    means, spreads = [f.mean() for f in frames], [f.std() for f in frames]
    card = shot_of(log, "card:212")
    behind = frame_at(short, card["start_s"] + card["seconds"] / 2)[:370].mean(axis=(0, 1))   # above the text
    tall = shot_of(log, "tall.png")
    edges = frame_at(short, tall["start_s"] + 0.3)
    edge_colours = [edges[5, 5], edges[5, W - 6], edges[H - 6, 5], edges[H - 6, W - 6]]
    layouts = [v["layout"] for v in log["visuals"]]
    print(f"       darkest moment mean {min(means):.0f}, flattest spread {min(spreads):.0f}; behind the number card "
          f"{behind.round()} (b.mp4 is purple {PURPLE}); layouts {layouts}")
    return (min(means) > 25 and min(spreads) > 5 and behind[2] > behind[1] + 15 and behind.max() > 25
            and card["behind"].startswith("b.mp4") and all(np.abs(e - np.array(AMBER)).max() < 60 for e in edge_colours)
            and layouts == ["framed", "card", "framed", "full", "card", "framed", "framed"]
            and "Look: 3 framed, 1 full screen, 2 text cards; word captions; grade on" in stdout
            and log["look"]["caption_style"] == "word" and tall["label"]["pos"] == "top")


@check
def look_render_frames_labels_and_marks():
    """in the same render: the photo shows whole inside its square card (the white square where the crop puts it), with a white border, its cyan label on the card's top edge and its red X on its spot; the video's card grows from 96 % to full size; the first and last frames match"""
    folder, log, _ = look_run()
    short = folder / "short.mp4"
    first = shot_of(log, "shot.png")
    picture = frame_at(short, first["start_s"] + 0.9)
    # The photo's white square (x 1060-1100, y 300-340) through the square crop (x 420-1500), drawn 880 px from (70, 520).
    sx, sy = 70 + (1060 - 420) / 1080 * 880, 520 + 300 / 1080 * 880
    square = box_of(picture[int(sy) - 30:int(sy) + 60, int(sx) - 30:int(sx) + 60].min(axis=2) > 225)
    border = (picture[516, 100:900].min(axis=1) > 225).mean()        # the top border, under the label's ends
    label = box_of(np.abs(picture[430:600] - np.array(CYAN)).max(axis=2) <= 40)
    mark = box_of((picture[..., 0] > 200) & (picture[..., 1] < 90) & (picture[..., 2] < 90))
    want_mark = (70 + (768 - 420) / 1080 * 880, 520 + 0.62 * 880)
    video = shot_of(log, "b.mp4")
    n0 = round(video["start_s"] * FPS)
    widths = []
    for n in (n0, n0 + 12):
        row = frame_number(short, n)[video["card"][1] + video["card"][3] // 2]
        white = np.nonzero(row.min(axis=1) > 225)[0]
        widths.append(int(white.max() - white.min() + 1) if len(white) else 0)
    seam = float(np.abs(frame_number(short, 0) - frame_number(short, log["durations"]["frames"] - 1)).mean())
    print(f"       white square at {square} in its window (expected near 30,30); top border white {border:.2f}; "
          f"label box {label}; X at {mark}, spot {want_mark[0]:.0f},{want_mark[1]:.0f}; video card width {widths}; "
          f"first vs last frame {seam:.2f}")
    return (square is not None and abs(square[0] - 30) <= 6 and abs(square[2] - 30) <= 6 and border > 0.6
            and label is not None and abs((label[0] + label[1]) / 2 - ex.CENTRE_X) <= 4
            and abs((label[2] + label[3]) / 2 + 430 - 510) <= 6
            and mark is not None and abs((mark[0] + mark[1]) / 2 - want_mark[0]) <= 6
            and abs((mark[2] + mark[3]) / 2 - want_mark[1]) <= 6
            and 850 <= widths[0] <= 880 and widths[1] == 900 and seam < 2.0)


@check
def look_render_hides_captions_over_cards():
    """in the same render: one yellow word at a time sits on WORD_Y while a picture shows; while a text card is up no caption event is on in the .ass, and the caption band has no yellow, nor cyan over the 212 card while its cyan words are said (the NOV card's own *NOV 10* is cyan); the .ass counts 212 up and draws every caption in Lilita One"""
    folder, log, _ = look_run()
    short = folder / "short.mp4"
    text = (folder / "captions.ass").read_text(encoding="utf-8")
    band = lambda f: f[ex.CAPTION_BANDS["word"][0]:ex.CAPTION_BANDS["word"][1]]
    words = log["word_times"]
    spans = [(card["start_s"], card["start_s"] + card["seconds"]) for card in (shot_of(log, "card:212"), shot_of(log, "card:~NOV"))]
    events = [(ass_seconds(m.group(1)), ass_seconds(m.group(2))) for m in re.finditer(r"^Dialogue: 2,([\d:.]+),([\d:.]+),", text, re.M)]
    overlaps = [(a, b) for a, b in events for s, e in spans if a < e - 0.01 and b > s + 0.01]
    # The cyan check means something only if a tagged (cyan) word is said while the 212 card is up.
    cyan_said = [w["text"] for w in words if spans[0][0] <= w["start_s"] < spans[0][1] and w["text"] in ("212", "GIGABYTES")]
    during = []
    for (s, e), cyan_too in zip(spans, (True, False)):
        for k in (0.25, 0.5, 0.75):
            f = band(frame_at(short, s + (e - s) * k))
            during.append(count_near(f, YELLOW, 40) + (count_near(f, CYAN, 30) if cyan_too else 0))
    first = shot_of(log, "shot.png")
    spoken = next(w for w in words if w["start_s"] > first["start_s"] + 0.3 and w["text"] not in ("212", "GIGABYTES"))
    lit = band(frame_at(short, spoken["start_s"] + 0.1))
    yellow = box_of(np.abs(lit - np.array(YELLOW)).max(axis=2) <= 40)
    captions = [line for line in text.splitlines() if line.startswith("Dialogue: 2,")]
    print(f"       caption-coloured px in the band while cards are up {during}; caption events over a card {overlaps}; "
          f"cyan words said over the 212 card {cyan_said}; {spoken['text']} yellow box {yellow}; "
          f"{len(captions)} caption events for {len(words)} words")
    return (max(during) == 0 and not overlaps and cyan_said and yellow is not None and abs((yellow[2] + yellow[3]) / 2 + ex.CAPTION_BANDS["word"][0]
                                                            - ex.WORD_Y) <= 6
            and all("\\fnLilita One" in c for c in captions) and len(captions) <= len(words)
            and ",,{\\" in text and re.search(r"\}212\s*$", text, re.M) and re.search(r"\}0\s*$", text, re.M))


@check
def old_example_scripts_still_read():
    """the PR 14-16 example scripts still read with no changes and keep their shots, focus, pans and weights (checked when the Rockstar files are on this PC)"""
    root = Path(r"C:\AI\gta")
    if not (root / "rockstar").is_dir():
        print("       C:\\AI\\gta\\rockstar isn't here; skipped")
        return True
    old = ex.load_script(HERE / "examples" / "nov10_preload.json", root)
    sample = ex.load_script(HERE / "examples" / "explainer_sample.json", root)
    first = old["lines"][0]["visuals"]
    pans = [v for line in old["lines"] for v in line["visuals"] if v.get("pan")]
    print(f"       nov10: {sum(len(l['visuals']) for l in old['lines'])} shots, layouts "
          f"{sorted({v.get('layout', 'card') for l in old['lines'] for v in l['visuals']})}; sample: "
          f"{sum(len(l['visuals']) for l in sample['lines'])} shots")
    return (first[0]["focus"] == 0.36 and first[0]["focus_y"] == 0.4 and first[1] == {**first[1], "kind": "card", "text": "212 GB"}
            and first[3]["kind"] == "video" and len(pans) == 3 and all(p["layout"] == "framed" for p in pans)
            and old["lines"][5]["visuals"][1]["weight"] == 2.2 and old["caption_style"] == "word")


# --- PR 18: beats ---

@functools.cache
def beat_assets():
    """ps5.webm: VP9 with the PS5's mislabel (limited-range pixels, a header that says full range, a container that
    says limited), made by flipping the header's flag on a normal VP9 file. twin.mp4 is the same picture in H.264."""
    assets()
    graph = (f"color=c={hexcolour(BLUE)}:s=1280x720:r={FPS}:d=4[bg];color=c={hexcolour(BOX)}:s=160x160:r={FPS}:d=4[b];"
             "[bg][b]overlay=x='300+60*t':y=280[v]")
    run_ffmpeg("-f", "lavfi", "-i", "anullsrc=r=48000:d=0.01", "-filter_complex", graph, "-map", "[v]", "-c:v", "libvpx-vp9",
               "-b:v", "0", "-crf", "18", "-pix_fmt", "yuv420p", ROOT / "plain_vp9.webm")
    run_ffmpeg("-i", ROOT / "plain_vp9.webm", "-c", "copy", "-bsf:v", "vp9_metadata=color_range=pc", "-color_range", "tv",
               ROOT / "ps5.webm")
    run_ffmpeg("-f", "lavfi", "-i", "anullsrc=r=48000:d=0.01", "-filter_complex", graph, "-map", "[v]", "-c:v", "libx264",
               "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", ROOT / "twin.mp4")
    return True


LOCK_BOX = [1000, 240, 160, 160]   # around shot.png's white square (x 1060-1100, y 300-340), which is its middle quarter


def placed_beat(raw, caption_style="word"):
    """A beat read, laid out and placed as load_script does, with a picture after it for a text card to blur."""
    beat_assets()
    visual = ex.parse_visual(raw, ROOT)
    ex.resolve_layout(visual, None)
    after = settled(look_shot())
    problems, warnings = ex.place_visuals([{"visuals": [visual, after]}], caption_style)
    assert not problems, problems
    return visual, warnings


def ink_box(frame, inside=None):
    """The box (x1, x2, y1, y2) around everything in a frame that isn't render_ass's mid grey."""
    return box_of(np.abs(frame - 128).max(axis=2) > 12)


def in_safe(box):
    return box is not None and box[0] >= SAFE[0] and box[1] <= SAFE[1] and box[2] >= SAFE[2] and box[3] <= SAFE[3]


@check
def beats_are_read():
    """each of the 7 beat types reads into the shot it draws: full fills the screen and keeps its focus; lockon takes its region in the picture's own pixels, centres the crop on it and tags it in red; card frames a picture (or is a text card); count is a number card; compare holds two 16:9 cards, labelled grey above and pink below unless told otherwise, and either may be a video; snippet keeps its words as typed and finds the highlighted ones; banner puts 1-3 words in capitals over a picture or a video; a line may call its shots "beats"; end_pill is read; a shot without a beat is what it was"""
    beat_assets()
    full = settled({"beat": "full", "file": "shot.png", "focus": [0.55, 0.3]})
    lock = settled({"beat": "lockon", "file": "shot.png", "box": LOCK_BOX, "label": "here"})
    card = settled({"beat": "card", "file": "shot.png", "label": "Card"})
    text = ex.parse_visual({"beat": "card", "card": "big news"}, ROOT)
    count = ex.parse_visual({"beat": "count", "card": "212 GB", "label": "PS5"}, ROOT)
    compare = settled({"beat": "compare", "top": {"file": "shot.png", "label": "GTA V"},
                       "bottom": {"file": "b.mp4", "at": "1", "label": {"text": "GTA VI", "style": "info"}}, "weight": 2})
    snippet = ex.parse_visual({"beat": "snippet", "text": "Pre-load opens on  November 10, nine days early.",
                               "highlight": ["november 10", "nine days"], "bg": "b.mp4@2"}, ROOT)
    banner = settled({"beat": "banner", "text": "the  catch", "file": "ps5.webm", "at": "1"})
    script = ex.load_script(write_script("beats_alias.json", title="Alias", end_pill=" @Test \u2022  Subscribe ", lines=[
        {"say": "One two three.", "beats": [{"beat": "full", "file": "shot.png"}, {"file": "shot.png"}]}]), ROOT)
    first, second = script["lines"][0]["visuals"]
    return (full["beat"] == "full" and full["layout"] == "full" and (full["focus"], full["focus_y"]) == (0.55, 0.3)
            and lock["layout"] == "full" and lock["lock"]["box"] == (1000.0, 240.0, 160.0, 160.0)
            and abs(lock["focus"] - 1080 / 1920) < 1e-9 and abs(lock["focus_y"] - 320 / 1080) < 1e-9
            and lock["lock"]["tag"]["text"] == "HERE" and lock["lock"]["tag"]["style"] == "no"
            and card["layout"] == "framed" and card["card_aspect"] == "1:1" and card["beat"] == "card"
            and text["kind"] == "card" and text["text"] == "BIG NEWS"
            and count["kind"] == "card" and count["beat"] == "count" and count["label"]["text"] == "PS5"
            and compare["kind"] == "compare" and compare["weight"] == 2 and compare["top"]["label"]["style"] == "grey"
            and compare["bottom"]["label"]["style"] == "info" and compare["bottom"]["kind"] == "video"
            and compare["bottom"]["start"] == 1 and compare["top"]["layout"] == "framed"
            and snippet["kind"] == "snippet" and snippet["text"] == "Pre-load opens on November 10, nine days early."
            and snippet["highlight"] == [(3, 5), (5, 7)] and snippet["bg"]["kind"] == "video"
            and banner["banner"]["text"] == "THE CATCH" and banner["banner"]["style"] == "number"
            and banner["kind"] == "video" and banner["layout"] == "full" and banner["size"] == (1280, 720)
            and first["beat"] == "full" and "beat" not in second and second["layout"] == "framed"
            and script["end_pill"]["text"] == "@Test \u2022 Subscribe")


@check
def bad_beats_stop():
    """an unknown beat, a setting a beat doesn't take, a missing file, box, text or side, a box outside the picture, a banner of 4 words or a bad style, a snippet of 19 words or with a highlight it doesn't hold, a count with no number, a card with both a file and a text, an end_pill that is too long and a line with both visuals and beats; and, once the script reads, a region too large for the screen, one that can't reach the safe area, snippet text that can't fit and a banner too wide to read: all listed, exit 2, nothing written"""
    beat_assets()
    out = ROOT / "bad_beats_out"
    bad = [{"beat": "zoom", "file": "shot.png"}, {"beat": "full", "file": "shot.png", "box": LOCK_BOX},
           {"beat": "full"}, {"beat": "lockon", "file": "shot.png"},
           {"beat": "lockon", "file": "shot.png", "box": [1800, 240, 300, 160]},
           {"beat": "lockon", "file": "shot.png", "box": [10, 20, 30]},
           {"beat": "banner", "file": "shot.png", "text": "one two three four"},
           {"beat": "banner", "file": "shot.png", "text": "x", "style": "pink"}, {"beat": "banner", "text": "x"},
           {"beat": "snippet", "text": " ".join(["word"] * 19)},
           {"beat": "snippet", "text": "A few words here", "highlight": ["missing"]},
           {"beat": "snippet", "text": "A few words here", "highlight": ["few words", "words here"]},
           {"beat": "count", "card": "NO NUMBER"}, {"beat": "compare", "top": {"file": "shot.png"}},
           {"beat": "compare", "top": {"file": "shot.png"}, "bottom": {"file": "shot.png", "pan": "right"}},
           {"beat": "compare", "top": "shot.png", "bottom": {"file": "shot.png"}},
           {"beat": "card", "file": "shot.png", "card": "X"}]
    first = run_tool(write_script("bad_beats.json", title="Bad beats", lines=[{"say": "One two three.", "beats": bad}]), out)
    second = run_tool(write_script("bad_beats2.json", title="Bad beats", end_pill="@" + "x" * 50, lines=[
        {"say": "One two three.", "visuals": ["shot.png"], "beats": ["shot.png"]}]), out)
    third = run_tool(write_script("bad_beats3.json", title="Bad beats", lines=[{"say": "One two three.", "beats": [
        {"beat": "lockon", "file": "shot.png", "box": [700, 240, 800, 160]},
        {"beat": "lockon", "file": "shot.png", "box": [1000, 10, 160, 100]},
        {"beat": "snippet", "text": "W" * 40 + " fits nowhere", "bg": "shot.png"},
        {"beat": "banner", "text": "WWWWWWWWWWWW WWWWWWWWWWWW WWWWWWWWWWWW", "file": "shot.png"}]}]), out)
    wanted = ["beat is one of full, lockon, card, compare, snippet, count, banner", "a full beat takes", "a full beat needs file",
              "a lockon beat needs box", "box is the region to lock on to", "a banner's text is 1-3 words",
              "a banner's style is", "a banner beat needs file", "a snippet is at most 18 words",
              "the highlight 'missing' isn't in the snippet's text", "two highlights share a word",
              "a count beat's card starts with the number", "a compare beat needs bottom",
              "a compare beat's bottom takes file, at, focus and label", "a compare beat's top is a picture",
              "a card beat has a file"]
    missing = [w for w in wanted if w not in first.stderr]
    missing += [w for w in ("end_pill is the subscribe pill's text", "beats is another name for visuals") if w not in second.stderr]
    missing += [w for w in ("pick a smaller region", "can't be brought inside the safe area", "doesn't fit a snippet card",
                            "is too wide to read at a glance") if w not in third.stderr]
    if missing:
        print(f"       missing from the errors: {missing}")
    return (first.returncode == second.returncode == third.returncode == 2 and not missing
            and first.stderr.count("line 1:") >= len(bad) and not out.exists())


@check
def lock_on_keeps_its_box_in_the_safe_area():
    """a lock-on's zoom is the largest up to 1.6 that keeps the box, its outline and its tag inside the safe area, and the camera zooms about a point that brings the region to the middle of the safe area's width, above the captions; on every frame of a 3 s shot the box is inside the frame; the zoom is 1 until the box lands, never goes back, and creeps on after the fast part; a region near the picture's edge takes a smaller zoom or its tag goes below; frame_time starts an event on its own frame"""
    visual, warnings = placed_beat({"beat": "lockon", "file": "shot.png", "box": LOCK_BOX, "label": "Here"})
    lock, frames = visual["lock"], 90
    zooms = [ex.lock_zoom(lock, n, frames) for n in range(frames)]
    rects = [ex.lock_rect(lock, z) for z in zooms]
    edge = ex.LOCK_EDGE
    in_frame = all(l - edge >= 0 and t - edge >= 0 and l + w + edge <= W and t + h + edge <= H for l, t, w, h in rects)
    l, t, w, h = rects[-1]
    tags = [ex.lock_tag_place(lock, r) for r in rects]
    (tw, th), tb = lock["tag"]["size"], lock["tag"]["border"]
    tag_safe = all(x - tw / 2 - tb >= SAFE[0] and x + tw / 2 + tb <= SAFE[1] + 1 and y - th / 2 - tb >= SAFE[2]
                   and y + th / 2 + tb <= SAFE[3] + 1 for x, y in tags)
    lands = ex.lock_frames(frames)[0]
    low, _ = placed_beat({"beat": "lockon", "file": "shot.png", "box": [1000, 620, 160, 240], "label": "Low"})
    low_rect = ex.lock_rect(low["lock"], low["lock"]["zoom"])
    # libass is given 33, 66, 100 ms...: an event that starts at frame_time(n) is on at frame n and not at n - 1.
    exact = all(ex.frame_time(n) * 1000 <= int(n * 1000 / FPS) - 1 or n == 0 for n in range(1, 400)) and \
        all(ex.frame_time(n + 1) * 1000 > int(n * 1000 / FPS) for n in range(400))
    print(f"       zoom {lock['zoom']:.2f} about {tuple(round(a) for a in lock['anchor'])}; box ends at "
          f"{tuple(round(v) for v in rects[-1])}, tag {lock['tag_at']}; a region low in the picture: zoom "
          f"{low['lock']['zoom']:.2f}, ends at {tuple(round(v) for v in low_rect)}; warnings {warnings}")
    return (abs(lock["zoom"] - ex.LOCK_ZOOM_MAX) < 1e-9 and in_frame and tag_safe and not warnings
            and l - edge >= SAFE[0] and l + w + edge <= SAFE[1] + 1 and t - edge >= SAFE[2] and t + h + edge <= SAFE[3] + 1
            and abs(l + w / 2 - ex.CENTRE_X) < 6 and t + h + edge < ex.CAPTION_BANDS["word"][0]
            and all(z == 1.0 for z in zooms[:lands + 1]) and all(b >= a for a, b in zip(zooms, zooms[1:]))
            and zooms[lands + round(ex.LOCK_ZOOM_S * FPS)] >= lock["zoom"] - 1e-6 and zooms[-1] > lock["zoom"]
            and low_rect[1] + low_rect[3] + edge <= SAFE[3] + 1 and low_rect[1] - edge >= SAFE[2] and exact)


@check
def compare_cards_stack_without_overlap():
    """a compare's two cards are the same size, 16:9, one above the other with a gap between them, both inside the safe area with their labels (the top card's on its top edge, the bottom card's on its bottom edge); the seam where the word captions go is in the gap; without labels the cards are no smaller; with phrase captions the captions hide instead"""
    labelled, _ = placed_beat({"beat": "compare", "top": {"file": "shot.png", "label": "GTA V"},
                               "bottom": {"file": "b.mp4", "label": "GTA VI"}})
    bare, _ = placed_beat({"beat": "compare", "top": {"file": "shot.png"}, "bottom": {"file": "b.mp4"}})
    ok = True
    for v in (labelled, bare):
        (tx, ty, tw, th), (bx, by, bw, bh) = v["top"]["frame"]["outer"], v["bottom"]["frame"]["outer"]
        cw, ch = v["top"]["frame"]["content"][2:]
        ok &= (tw, th) == (bw, bh) and tx == bx and ty + th + 20 <= by and ty + th < v["seam_y"] < by
        ok &= tx >= SAFE[0] and tx + tw <= SAFE[1] + 1 and ty >= SAFE[2] and by + bh <= SAFE[3] + 1
        ok &= abs(cw / ch - 16 / 9) < 0.01 and abs(tx + tw / 2 - ex.CENTRE_X) <= 1
    top, bottom = labelled["top"]["label"], labelled["bottom"]["label"]
    reach = lambda label: ((label["y"] - ex.label_reach(label)[0] * ex.LABEL_POP_PEAK),
                           (label["y"] + ex.label_reach(label)[1] * ex.LABEL_POP_PEAK))
    slot = {"start": 30, "end": 120, "visual": labelled}
    print(f"       with labels: top {labelled['top']['frame']['outer']}, bottom {labelled['bottom']['frame']['outer']}, seam "
          f"{labelled['seam_y']}, labels at y {top['y']} and {bottom['y']}; without: {bare['top']['frame']['outer']}")
    return (ok and top["y"] == labelled["top"]["frame"]["outer"][1] and reach(top)[0] >= SAFE[2]
            and bottom["y"] == labelled["bottom"]["frame"]["outer"][1] + labelled["bottom"]["frame"]["outer"][3]
            and reach(bottom)[1] <= SAFE[3] + 1 and bare["top"]["frame"]["outer"][3] >= labelled["top"]["frame"]["outer"][3]
            and ex.caption_places([slot]) == [(1.0, 4.0, labelled["seam_y"])]
            and ex.card_spans([slot], "word") == [] and ex.card_spans([slot], "phrase") == [(1.0, 4.0)])


YELLOW_MARK = (255, 230, 0)


@check
def snippet_text_fits_its_card():
    """a snippet's words sit inside its white card with room on every side, as large as fits (3 words at the largest size, a full 18-word sentence smaller), the card is 900 px wide inside the safe area and ends above the captions; on real pixels the yellow marker isn't there at first, grows from its left end, and ends behind the highlighted words inside the card; the text is as typed, never in capitals"""
    texts = [("Unlocks at midnight.", ["midnight"]),
             ("Pre-load opens on November 10, a full nine days before the game unlocks.", ["November 10"]),
             ("Rockstar confirmed that pre-loading begins on November 10 for digital PlayStation 5 owners, nine days before "
              "worldwide launch.", ["nine days before worldwide launch"])]
    notes, ok, sizes = [], True, []
    for n, (text, marked) in enumerate(texts):
        visual, _ = placed_beat({"beat": "snippet", "text": text, "highlight": marked, "bg": "shot.png"})
        s = visual["snippet"]
        x, y, w, h = s["card"]
        bar = s["bars"][0]
        slot = {"start": 0, "end": 90, "frames": 90, "visual": visual}
        events = ex.snippet_events(slot, settled=True)
        early, mid, late = render_ass(events, [0.2, bar["at"] + bar["seconds"] / 2, 2.5], f"snippet{n}")
        inside = lambda f: f[y:y + h, x:x + w]
        card = box_of(late.min(axis=2) > 240)
        # The dark text, looked for 30 px in from the card's edges: its rounded corners show the shadow under them.
        deep = box_of(inside(late)[30:-30, 30:-30].max(axis=2) < 90)
        words = deep and tuple(v + 30 for v in deep)
        # The marker on the first highlighted line (a phrase that runs over a line's end gets a bar on each line).
        rows = slice(int(bar["box"][1]) - y, int(bar["box"][3]) - y + 1)
        marker = [box_of(np.abs(inside(f)[rows] - np.array(YELLOW_MARK)).max(axis=2) <= 40) for f in (early, mid, late)]
        sizes.append(s["size"])
        notes.append(f"{len(text.split())} words: size {s['size']}, {len(s['lines'])} lines, card y {y}-{y + h}, text "
                     f"{words} in a {w}x{h} card, marker {marker}")
        ok &= (w == 900 and x == 60 and y >= SAFE[2] and y + h + ex.CARD_SHADOW_DY < ex.CAPTION_BANDS["word"][0]
               and card is not None and abs(card[0] - x) <= 2 and abs(card[1] - (x + w - 1)) <= 2 and abs(card[2] - y) <= 2
               and words is not None and words[0] >= 40 and words[1] <= w - 40 and words[2] >= 30 and words[3] <= h - 30
               and marker[0] is None and marker[1] is not None and marker[2] is not None
               and abs(marker[1][0] - marker[2][0]) <= 3 and marker[1][1] < marker[2][1] - 20
               and marker[2][0] >= 20 and marker[2][1] <= w - 20
               and ex.escape_ass(text.split()[0]) in events[-len(s["lines"])])
    print("       " + "; ".join(notes))
    return ok and sizes[0] == ex.SNIPPET_SIZE and sizes[2] <= sizes[1] <= sizes[0] and sizes[2] >= ex.SNIPPET_MIN_SIZE


@check
def banner_and_pill_stay_in_the_safe_area():
    """on real pixels, with outline and shadow: a banner's pill is inside the safe area even at the top of its pop, and a banner of three long words is drawn smaller to stay there (never under size 48); the subscribe pill is inside the safe area under the captions (and at the top when a label is in its way), its cursor stays inside too, it is white before the click and pink after, and nothing of it is left on the last frames"""
    ok, notes = True, []
    for text in ("The catch", "Completely different now"):
        visual, _ = placed_beat({"beat": "banner", "text": text, "file": "shot.png"})
        slot = {"start": 0, "end": 60, "frames": 60, "visual": visual}
        peak, settled_frame = render_ass(ex.banner_events(slot), [ex.BANNER_DELAY_S + 0.14, 1.0], "banner" + text[:3])
        boxes = [ink_box(peak), ink_box(settled_frame)]
        notes.append(f"{text[:9]!r} at size {visual['banner']['font']}: {boxes}")
        ok &= all(in_safe(b) for b in boxes) and (boxes[0][1] - boxes[0][0]) >= (boxes[1][1] - boxes[1][0])
        ok &= (visual["banner"]["font"] == ex.BANNER_SIZE if text == "The catch"
               else ex.BANNER_MIN_SIZE <= visual["banner"]["font"] < ex.BANNER_SIZE)
    total = 150
    script = ex.load_script(write_script("pill.json", title="Pill", end_pill="@NeonGetaway \u2022 Subscribe",
                                         lines=[{"say": "One two three.", "visuals": ["shot.png"]}]), ROOT)
    pill = script["end_pill"]
    plain_slot = {"start": 0, "end": total, "visual": script["lines"][0]["visuals"][0]}
    labelled, _ = placed_beat({"file": "shot.png", "label": {"text": "Low", "pos": "bottom"}})
    f_in, f_click, f_out = ex.pill_frames(total)
    for slot, want in ((plain_slot, "bottom"), ({"start": 0, "end": total, "visual": labelled}, "top")):
        warning = ex.place_end_pill(pill, [slot], total)
        times = [f / FPS + 0.004 for f in range(f_in, total, 3)] + [(total - 2) / FPS + 0.004, (total - 1) / FPS + 0.004]
        frames = render_ass(ex.end_pill_events(pill, total), times, "pill" + want)
        boxes = [ink_box(f) for f in frames]
        shown = [b for b in boxes if b is not None]
        before = frames[times.index(next(t for t in times if t > (f_click - 6) / FPS))]
        after = frames[times.index(next(t for t in times if t > (f_click + 8) / FPS))]
        white, pink = int((before.min(axis=2) > 240).sum()), count_near(after, PINK, 40)
        notes.append(f"pill at the {pill['pos']} (y {pill['y']}): ink x {min(b[0] for b in shown)}-{max(b[1] for b in shown)}, "
                     f"y {min(b[2] for b in shown)}-{max(b[3] for b in shown)}; white px before the click {white}, pink "
                     f"after {pink}; last two frames {boxes[-2:]}")
        ok &= (warning is None and pill["pos"] == want and all(in_safe(b) for b in shown) and len(shown) > 10
               and boxes[-1] is None and boxes[-2] is None and white > 5000 and pink > 5000
               and count_near(before, PINK, 40) == 0)
    print("       " + "; ".join(notes))
    return ok


@check
def beat_sounds_are_planned_and_sit_under_the_voice():
    """a compare gets a low whoosh for its first card and a higher one as the second lands, a lock-on a click as its box lands (which never pushes the line's whoosh out), a banner and a snippet a hit, and the subscribe pill its click; the loudest 400 ms of the lock-on click and of the pill click is 8-18 LU under the voice's -14 LUFS (they are short, so quieter by this measure than they sound) with nothing at the test tones, and the low whoosh really is lower than the high one"""
    beat_assets()
    image = {"source": "img", "kind": "image"}
    lock = {"source": "lock", "kind": "image", "lock": {"zoom": 1.5}}
    compare = {"source": "compare", "kind": "compare"}
    banner = {"source": "banner", "kind": "image", "banner": {"text": "X"}}
    snippet = {"source": "snippet", "kind": "snippet"}
    slots = [{"line": 0, "start": 0, "end": 60, "visual": image}, {"line": 1, "start": 60, "end": 150, "visual": lock},
             {"line": 1, "start": 150, "end": 240, "visual": compare}, {"line": 2, "start": 240, "end": 300, "visual": banner},
             {"line": 2, "start": 300, "end": 390, "visual": snippet},
             {"line": 2, "start": 390, "end": 435, "visual": image, "loop_tail": True}]
    got = [(e["kind"], e.get("pitch"), e["at_s"]) for e in ex.plan_sfx(slots, 13.4)]
    want = [("whoosh", None, 2.0), ("lock", None, 2.0 + ex.LOCK_SNAP_S), ("whoosh", "low", 5.0),
            ("whoosh", "high", round(5.0 + ex.COMPARE_SECOND_S + ex.COMPARE_SLIDE_S / 2, 3)), ("hit", None, 8.0),
            ("hit", None, 10.0), ("click", None, 13.4)]

    def level(sound, name):
        import wave
        path = ROOT / name
        padded = np.concatenate([np.zeros(ex.RATE // 2, np.float32), sound, np.zeros(ex.RATE, np.float32)])
        with wave.open(str(path), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(ex.RATE)
            w.writeframes((np.repeat(padded[:, None], 2, 1) * 32767).astype(np.int16).tobytes())
        return momentary_max(path), max(band_level(path, GAME_HZ), band_level(path, MUSIC_HZ))
    lock_level, click_level = level(ex.lock_click(0), "lock_click.wav"), level(ex.mouse_click(0), "mouse_click.wav")
    centre = lambda sound: float((np.abs(np.fft.rfft(sound)) ** 2 * np.fft.rfftfreq(len(sound), 1 / ex.RATE)).sum()
                                 / (np.abs(np.fft.rfft(sound)) ** 2).sum())
    low, usual, high = (centre(ex.whoosh(0, ex.WHOOSH_PITCH.get(p, 1.0))) for p in ("low", None, "high"))
    track = ex.sfx_track([{"kind": "lock", "at_s": 1.0}, {"kind": "click", "at_s": 2.0}], 3 * ex.RATE)
    loud = np.abs(track)
    starts = [np.flatnonzero(loud[a:b] > loud[a:b].max() * 0.05)[0] / ex.RATE + a / ex.RATE
              for a, b in ((0, int(1.5 * ex.RATE)), (int(1.5 * ex.RATE), 3 * ex.RATE))]
    print(f"       planned {got}; loudest 400 ms: lock-on click {lock_level[0]:.1f} LUFS, pill click {click_level[0]:.1f} LUFS "
          f"(voice -14); at the test tones {max(lock_level[1], click_level[1]):.0f} dB; whoosh centres {low:.0f}, {usual:.0f}, "
          f"{high:.0f} Hz; clicks start at {starts[0]:.3f} s (1.0) and {starts[1]:.3f} s (2.0)")
    return (len(got) == len(want) and all(k == wk and p == wp and abs(a - wa) < 0.002 for (k, p, a), (wk, wp, wa) in zip(got, want))
            and -32 <= lock_level[0] <= -22 and -32 <= click_level[0] <= -22 and max(lock_level[1], click_level[1]) < -70
            and low < usual * 0.8 and high > usual * 1.2 and abs(starts[0] - 1.0) < 0.003 and abs(starts[1] - 2.0) < 0.003)


@check
def pacing_counts_changes_and_warns():
    """a visual change is a cut or something arriving inside a shot (a compare's second card, a mark, the subscribe pill); changes within 0.5 s of the one before count with it, so a lock-on box or a marker sweep that follows its cut at once is that cut, and so is a second mark; the report gives the length, the count, the rate every 10 s and the longest gap; a rate under 4 or over 6 per 10 s warns and so does a gap over 3 s (3.0 s itself doesn't); nothing fails"""
    image = {"source": "img", "kind": "image"}
    slots = [{"start": 0, "end": 60, "visual": image},
             {"start": 60, "end": 150, "visual": {"source": "lock", "kind": "image", "lock": {"zoom": 1.5}}},
             {"start": 150, "end": 240, "visual": {"source": "compare", "kind": "compare"}},
             {"start": 240, "end": 330, "visual": {"source": "snippet", "kind": "snippet", "snippet": {"sweeps": [0.4, 1.0]}}},
             {"start": 330, "end": 336, "visual": image},
             {"start": 336, "end": 420, "visual": {"source": "marked", "kind": "image",
                                                    "marks": [{"icon": "x", "at": 0.5}, {"icon": "check", "at": 0.6}]}},
             {"start": 420, "end": 465, "visual": image, "loop_tail": True}]
    changes = ex.visual_changes(slots, 465)
    times = [at for at, _ in changes]
    want = [2.0, 5.0, 5.0 + ex.COMPARE_SECOND_S, 8.0, 11.0, 11.7, round((465 - 60) / FPS, 3), 14.0]
    late_box = ex.visual_changes([slots[0], {"start": 60, "end": 150, "visual": {"source": "late", "kind": "image",
                                                                                 "marks": [{"icon": "x", "at": 1.2}]}}])
    report = ex.pacing_report(changes, 15.5, 40)
    slow = ex.pacing_report([(3.0, "cut"), (6.5, "cut"), (9.5, "cut")], 12.5)
    fast = ex.pacing_report([(k * 1.0, "cut") for k in range(1, 20)], 20.0)
    lines = ex.pacing_lines(slow)
    print(f"       changes at {times}; report: {report['changes']} changes, {report['per_10_s']} per 10 s, longest gap "
          f"{report['longest_gap_s']} s at {report['longest_gap_at_s']}; slow warnings {slow['warnings']}")
    return (len(times) == len(want) and all(abs(a - b) < 0.002 for a, b in zip(times, want))
            and report["changes"] == 8 and abs(report["per_10_s"] - 8 / 15.5 * 10) < 0.01
            and report["longest_gap_s"] == 3.0 and report["longest_gap_at_s"] == [8.0, 11.0] and report["words"] == 40
            and report["warnings"] == [] and not report["length_ok"] and ex.CHANGE_MERGE_S == 0.5
            and ex.LOCK_SNAP_S < ex.CHANGE_MERGE_S < ex.COMPARE_SECOND_S and [at for at, _ in late_box] == [2.0, 3.2]
            and slow["per_10_s"] == 2.4 and len(slow["warnings"]) == 2 and "under the target" in slow["warnings"][0]
            and "3.5 s, from 3.0 s to 6.5 s" in slow["warnings"][1] and slow["longest_gap_s"] == 3.5
            and any("over the target" in w for w in fast["warnings"]) and len(fast["warnings"]) == 1
            and ex.pacing_report([(k * 2.0, "cut") for k in range(1, 18)], 36.0)["warnings"] == []
            and any("WARN" in line for line in lines) and "Longest gap   3.5 s" in "\n".join(lines))


@check
def ps5_video_gets_the_range_fix():
    """a VP9 file with the PS5's mislabel renders with the same colours as its H.264 twin, because it gets clip.py's fix (without the fix its picture is washed out, which this check also renders to be sure it can tell); the real test1.webm, when it is on this PC, reads as 1920x1080 and gets the fix"""
    import clip
    beat_assets()
    encode = ex.pick_encoder()[1]

    def first_frame(name, out):
        visual = settled({"beat": "full", "file": name})
        slot = {"visual": visual, "frames": 6, "start_s": 0.0, "fill": "play"}
        ex.render_segment(slot, ROOT / out, encode)
        return frame_number(ROOT / out, 3)
    fixed, twin = first_frame("ps5.webm", "range_fixed.mp4"), first_frame("twin.mp4", "range_twin.mp4")
    keep = ex.source_args
    try:
        ex.source_args = lambda path: []
        washed = first_frame("ps5.webm", "range_washed.mp4")
    finally:
        ex.source_args = keep
    good, bad = float(np.abs(fixed - twin).mean()), float(np.abs(washed - twin).mean())
    real = Path(r"C:\AI\gta\input\test1.webm")
    real_ok = True
    if real.is_file():
        visual = ex.parse_visual({"beat": "full", "file": "input/test1.webm", "at": "20"}, Path(r"C:\AI\gta"))
        real_ok = visual["size"] == (1920, 1080) and ex.source_args(real) == clip.RANGE_FIX_ARGS
        print(f"       test1.webm reads as {visual['size']}, fix {ex.source_args(real)}")
    else:
        print("       C:\\AI\\gta\\input\\test1.webm isn't here; that part is skipped")
    print(f"       mean difference from the H.264 twin: {good:.2f} with the fix, {bad:.2f} without; a corner pixel "
          f"{fixed[40, 40].tolist()} / twin {twin[40, 40].tolist()} / without the fix {washed[40, 40].tolist()}")
    return (ex.source_args(ROOT / "ps5.webm") == clip.RANGE_FIX_ARGS and ex.source_args(ROOT / "twin.mp4") == []
            and clip.needs_range_fix("vp9", "tv") and not clip.needs_range_fix("h264", "tv")
            and not clip.needs_range_fix("vp9", "pc") and good < 3.0 and bad > 6.0 and real_ok)


@functools.cache
def beats_run():
    """One script with every beat type, video backgrounds (the mislabelled VP9 full screen and behind a snippet, an
    H.264 clip in a compare's bottom card), an opening on a 1 s clip used from its start, and the subscribe pill. The
    grade is off so that colours can be told apart exactly; the look's own render checks the grade."""
    beat_assets()
    script = write_script("beats.json", title="Beats test", grade=False, end_pill="@Test \u2022 Subscribe", lines=[
        {"say": "A short clip opens this test, and then a red box locks on to the white square.",
         "beats": [{"beat": "full", "file": "stops.mp4"},
                   {"beat": "lockon", "file": "shot.png", "box": LOCK_BOX, "label": {"text": "Here", "style": "info"},
                    "weight": 2.5}]},
        {"say": "Here is the catch, and two cards compare an old thing with a new thing right now.",
         "beats": [{"beat": "banner", "text": "The catch", "file": "ps5.webm", "at": "0.5"},
                   {"beat": "compare", "top": {"file": "shot.png", "label": "Old"},
                    "bottom": {"file": "b.mp4", "at": "1", "label": "New"}, "weight": 2.5}]},
        {"say": "A snippet card shows plain words with a marker, and then 212 gigabytes counts up.",
         "beats": [{"beat": "snippet", "text": "Pre-load opens on November 10, nine days before launch.",
                    "highlight": ["November 10"], "bg": "ps5.webm@1", "weight": 2},
                   {"beat": "count", "card": "212 GB"}]},
        {"say": "A square card comes last, and then the pill shows at the very end of it all.",
         "beats": [{"beat": "card", "file": "shot.png"}, {"beat": "full", "file": "tall.png"}]}])
    result = run_tool(script)
    assert result.returncode == 0, result.stderr[-800:]
    folder = Path(re.search(r"Run folder: (.+)", result.stdout).group(1).strip())
    return folder, json.loads((folder / "log.json").read_text(encoding="utf-8")), result.stdout


def beat_shot(log, beat):
    return next(v for v in log["visuals"] if v["beat"] == beat and not v["loop_tail"])


@check
def beats_render_lock_on_follows_the_zoom():
    """a real render with every beat type: while the camera zooms, the red box stays centred on the picture's white square and keeps its size next to it (the box is 4 squares wide), it grows by the lock-on's zoom, and at the end the box and its tag are inside the safe area; before the box lands there is no red on screen; the lock-on's click is in the log at the moment it lands"""
    folder, log, stdout = beats_run()
    short = folder / "short.mp4"
    lock = beat_shot(log, "lockon")
    start, end = lock["start_s"], lock["start_s"] + lock["seconds"]
    red = lambda f: box_of((f[..., 0] > 200) & (f[..., 1] < 90) & (f[..., 2] < 90))
    before = red(frame_at(short, start + 0.15))
    rows, ok = [], True
    for t in (start + ex.LOCK_SNAP_S + 0.1, start + ex.LOCK_SNAP_S + 0.4, end - 0.12):
        frame = frame_at(short, t)
        box = red(frame)
        if box is None:
            return False
        inner = frame[box[2] + 30:box[3] - 30, box[0] + 30:box[1] - 30]
        square = box_of(inner.min(axis=2) > 215)
        if square is None:
            return False
        sx, sy = box[0] + 30 + (square[0] + square[1]) / 2, box[2] + 30 + (square[2] + square[3]) / 2
        ratio = (box[1] - box[0] + 1 - 2 * ex.LOCK_STROKE) / (square[1] - square[0] + 1)
        rows.append((round(t, 2), box, round(sx), round(sy), round(ratio, 2)))
        ok &= (abs(sx - (box[0] + box[1]) / 2) <= 5 and abs(sy - (box[2] + box[3]) / 2) <= 5 and abs(ratio - 4) <= 0.3
               and box[0] >= 0 and box[1] < W and box[2] >= 0 and box[3] < H)
    last = frame_at(short, end - 0.12)
    tag = box_of(np.abs(last - np.array(CYAN)).max(axis=2) <= 40)
    final = rows[-1][1]
    grew = (final[1] - final[0]) / (rows[0][1][1] - rows[0][1][0])
    clicks = [e for e in log["sfx"] if e["kind"] == "lock"]
    print(f"       red before the box lands {before}; (time, red box, square centre x, y, box width in squares) {rows}; "
          f"grew {grew:.2f}x (zoom {lock['lock']['zoom']}); tag {tag}; click {clicks}")
    return (ok and before is None and in_safe(final) and in_safe(tag) and grew > 1.2 and lock["motion"] == "lock_on"
            and abs(final[0] - 8 - lock["lock"]["box_at_end"][0]) <= 16
            and len(clicks) == 1 and abs(clicks[0]["at_s"] - (start + ex.LOCK_SNAP_S)) < 0.002
            and "1 lock-on clicks, 1 pill clicks" in stdout
            and "Beats: 2 full, 1 lockon, 1 card, 1 compare, 1 snippet, 1 count, 1 banner; subscribe pill" in stdout)


@check
def beats_render_compare_slides_in_and_captions_move():
    """in the same render: the compare's top card shows its picture from the start and the bottom card's place is still background; 1.3 s in the bottom card is there with its video, the two cards have white borders with background between them, and the labels are on the outer edges (grey above, pink below); a word said while both are up is drawn on the seam between the cards, not in the usual caption band; two whooshes, low then high"""
    folder, log, _ = beats_run()
    short = folder / "short.mp4"
    shot = beat_shot(log, "compare")
    start, info = shot["start_s"], shot["compare"]
    (tx, ty, tw, th), (bx, by, bw, bh) = info["top"], info["bottom"]
    early, late = frame_at(short, start + 0.45), frame_at(short, start + 1.5)
    # 40 % across each card: on shot.png that is plain amber, clear of its green left band and its white square.
    top_middle = lambda f: f[ty + th // 2 - 40, tx + tw * 2 // 5].tolist()
    bottom_middle = lambda f: f[by + bh // 2 + 60, bx + tw * 2 // 5].tolist()
    column = late[ty:by + bh, tx + 60]                                               # down the left of both cards
    white = column.min(axis=1) > 225
    gap = (~white)[th - 4:by - ty + 4].sum()                                         # not white between the two borders
    pink = box_of(np.abs(late - np.array(PINK)).max(axis=2) <= 40)
    grey = box_of(np.abs(late[:ty + 80] - np.array(ex.GREY)).max(axis=2) <= 30)
    words = [w for w in log["word_times"] if start + ex.COMPARE_SECOND_S + 0.4 <= w["start_s"] and w["end_s"] <= start + shot["seconds"] - 0.2]
    word = max(words, key=lambda w: w["end_s"] - w["start_s"])
    said = frame_at(short, word["start_s"] + 0.12)
    yellow = np.abs(said - np.array(YELLOW)).max(axis=2) <= 40
    seam, usual = box_of(yellow), int(yellow[ex.CAPTION_BANDS["word"][0] + 60:ex.CAPTION_BANDS["word"][1]].sum())
    whooshes = [(e.get("pitch"), e["at_s"]) for e in log["sfx"] if e["kind"] == "whoosh" and start - 0.01 <= e["at_s"] < start + 2]
    print(f"       top card middle {top_middle(early)} then {top_middle(late)} (amber {AMBER}); bottom card's place "
          f"{bottom_middle(early)} then {bottom_middle(late)} (purple {PURPLE}); rows of background between the borders {gap}; "
          f"labels: grey {grey}, pink {pink}; {word['text']} yellow box {seam} (seam y {info['seam_y']}); whooshes {whooshes}")
    amber = lambda c: abs(c[0] - AMBER[0]) < 40 and abs(c[1] - AMBER[1]) < 40 and c[2] < 60
    purple = lambda c: c[2] > c[1] + 60 and c[2] > c[0] + 30
    return (amber(top_middle(early)) and amber(top_middle(late)) and not purple(bottom_middle(early))
            and purple(bottom_middle(late)) and white[:8].any() and white[-8:].any() and gap >= 10
            and pink is not None and abs((pink[2] + pink[3]) / 2 - info["label_ys"][1]) <= 8 and in_safe(pink)
            and grey is not None and abs((grey[2] + grey[3]) / 2 - info["label_ys"][0]) <= 8 and grey[2] >= SAFE[2]
            and seam is not None and abs((seam[2] + seam[3]) / 2 - info["seam_y"]) <= 12 and usual == 0
            and [p for p, _ in whooshes] == ["low", "high"] and abs(whooshes[0][1] - start) < 0.002)


@check
def beats_render_video_backgrounds_and_cards():
    """in the same render: the banner's pink pill is over the mislabelled VP9 clip, whose blue comes out as it should, full screen; the snippet's white card with its dark words and yellow marker is over that clip blurred; the count card counts to 212; the square card is framed as before; no moment of the Short is black or flat"""
    folder, log, _ = beats_run()
    short = folder / "short.mp4"
    banner, snippet, count, card = (beat_shot(log, b) for b in ("banner", "snippet", "count", "card"))
    shown = frame_at(short, banner["start_s"] + 0.8)
    corners = [shown[40, 40], shown[40, W - 41], shown[H - 41, 40], shown[H - 41, W - 41]]
    pill = box_of(np.abs(shown - np.array(PINK)).max(axis=2) <= 40)
    late = frame_at(short, snippet["start_s"] + snippet["seconds"] - 0.25)
    x, y, w, h = snippet["snippet"]["card"]
    inside = late[y:y + h, x:x + w]
    white, dark = float((inside.min(axis=2) > 235).mean()), int((inside.max(axis=2) < 90).sum())
    marker = box_of(np.abs(inside - np.array(YELLOW_MARK)).max(axis=2) <= 40)
    behind = late[200:340].mean(axis=(0, 1))                                          # above the card: the blurred clip
    text = (folder / "captions.ass").read_text(encoding="utf-8")
    times = [t / 4 for t in range(int(log["durations"]["video_s"] * 4))]
    frames = [frame_at(short, t)[::8, ::8] for t in times]
    print(f"       banner corners {[c.tolist() for c in corners]} (blue {BLUE}), pill {pill}; snippet card {white:.2f} white, "
          f"{dark} dark px, marker {marker}, behind it {behind.round()}; darkest moment {min(f.mean() for f in frames):.0f}, "
          f"flattest {min(f.std() for f in frames):.0f}; layouts {[v['layout'] for v in log['visuals']]}")
    return (all(np.abs(c - np.array(BLUE)).max() < 12 for c in corners) and pill is not None and in_safe(pill)
            and abs((pill[2] + pill[3]) / 2 - ex.BANNER_Y) <= 6 and banner["banner"] == "THE CATCH"
            and white > 0.6 and dark > 2000 and marker is not None and behind[2] > behind[0] + 15
            and snippet["behind"].startswith("ps5.webm") and re.search(r"\}212\s*$", text, re.M)
            and count["layout"] == "card" and card["layout"] == "framed" and card["card"] == [60, 510, 900, 900]
            and min(f.mean() for f in frames) > 12 and min(f.std() for f in frames) > 5
            and [v["layout"] for v in log["visuals"]] == ["full", "full", "full", "compare", "snippet", "card", "framed", "full",
                                                          "full"][:len(log["visuals"])])


@check
def beats_render_never_ends_frozen_and_still_loops():
    """in the same render, which opens on a 1 s clip used from its start: the loop ending plays that clip (the log says it runs backwards), the picture moves in every quarter of the last 1.5 s, the last frame matches the first frame, the subscribe pill is white, then pink after its click, and gone by the last frame; the pill's click is in the log"""
    folder, log, _ = beats_run()
    short = folder / "short.mp4"
    total = log["durations"]["frames"]
    tail = log["visuals"][-1]
    stills = [frame_number(short, n) for n in (total - 45, total - 34, total - 23, total - 12, total - 1)]
    region = lambda f: f[380:600]                                    # where stops.mp4's box moves, above the captions
    moved = [float((np.abs(region(a) - region(b)).max(axis=2) > 40).mean()) for a, b in zip(stills, stills[1:])]
    first = frame_number(short, 0)
    lo, hi = ex.CAPTION_BANDS["word"]             # the last word's caption may still be up there, and the first word's is
    picture = lambda f: np.concatenate([f[:lo], f[hi:]])
    seam, whole = float(np.abs(picture(first) - picture(stills[-1])).mean()), float(np.abs(first - stills[-1]).mean())
    pill = log["end_pill"]
    band = lambda f: f[pill["y"] - 46:pill["y"] + 46, 200:820]
    before = band(frame_at(short, pill["click_s"] - 0.25))
    after = band(frame_at(short, pill["click_s"] + 0.3))
    white, pink = int((before.min(axis=2) > 235).sum()), count_near(after, PINK, 40)
    left = float(np.abs(band(first) - band(stills[-1])).mean())
    clicks = [e["at_s"] for e in log["sfx"] if e["kind"] == "click"]
    print(f"       loop ending: {tail['source']}, fill {tail['fill']}; share of the picture that moves in each quarter "
          f"{[round(m, 4) for m in moved]}; first vs last frame {seam:.2f} outside the caption band ({whole:.2f} with the "
          f"captions' own words in it); pill at the {pill['pos']}: white px {white} "
          f"before the click, pink {pink} after, first vs last frame there {left:.2f}; click {clicks}")
    return (tail["loop_tail"] and tail["fill"] == "bounce" and tail["source"] == "stops.mp4" and min(moved) > 0.002
            and seam < 2.0 and white > 3000 and pink > 3000 and left < 2.0 and clicks == [pill["click_s"]]
            and abs(pill["click_s"] - (total - round(ex.PILL_CLICK_S * FPS)) / FPS) < 0.002
            and int(probe(short)["streams"][0]["nb_read_frames"]) == total)


@check
def beats_render_prints_the_pacing_check():
    """in the same render: the console prints the pacing check (length, changes, rate, longest gap), the log holds the same numbers with every change it counted (the compare's second card and the pill among them; the lock-on box and the marker count with their cuts), and a Short this short gets the under-34 s warning without failing"""
    folder, log, stdout = beats_run()
    pacing = log["pacing"]
    what = " | ".join(e["what"] for e in pacing["events"])
    again = subprocess.run([sys.executable, str(HERE / "explainer.py"), "--pacing", str(folder)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
    cuts = len(log["visuals"]) - 1
    print(f"       {pacing['changes']} changes ({cuts} cuts) in {pacing['length_s']} s: {pacing['per_10_s']} per 10 s, longest "
          f"gap {pacing['longest_gap_s']} s; warnings {[w[:40] for w in log['warnings']]}")
    return ("Pacing check:" in stdout and f"Changes       {pacing['changes']} visual changes" in stdout
            and re.search(r"Length\s+\d+\.\d s\s+\(target 34-39 s\)\s+WARN", stdout)
            and re.search(r"Rate\s+\d+\.\d per 10 s\s+\(target 4-6\)", stdout) and re.search(r"Longest gap\s+\d+\.\d s, from", stdout)
            and pacing["changes"] == len(pacing["events"]) and 4 <= pacing["changes"] <= cuts + 2
            and abs(pacing["length_s"] - log["durations"]["video_s"]) < 0.05
            and abs(pacing["per_10_s"] - pacing["changes"] / pacing["length_s"] * 10) < 0.02
            and all(k in what for k in ("second card slides in", "subscribe pill pops in", "loop ending"))
            and "lock-on box lands" not in what and "marker sweeps" not in what
            and any("under the 34-39 s target" in w for w in log["warnings"])
            and again.returncode == 0 and "Pacing check:" in again.stdout and "the second card slides in" in again.stdout)


@check
def beat_example_scripts_read():
    """the two PR 18 example scripts read (checked when the Rockstar files and test1.webm are on this PC): beats_demo.json uses every beat type, has a subscribe pill, opens on a clip and holds only Rockstar's files; the video test is marked TEST ONLY in its file name, title, banner and pill and plays the GTA 5 recording behind every beat; the PR 17 example reads as before, with no beats"""
    root = Path(r"C:\AI\gta")
    if not (root / "rockstar").is_dir() or not (root / "input" / "test1.webm").is_file():
        print("       C:\\AI\\gta\\rockstar or input\\test1.webm isn't here; skipped")
        return True
    demo = ex.load_script(HERE / "examples" / "beats_demo.json", root)
    test = ex.load_script(HERE / "examples" / "beats_video_test_TEST_ONLY.json", root)
    old = ex.load_script(HERE / "examples" / "nov10_preload_v2.json", root)
    shots = lambda script: [v for line in script["lines"] for v in line["visuals"]]
    files = lambda v: ([v["path"]] if "path" in v else []) + [f for k in ("top", "bottom", "bg") if k in v for f in files(v[k])]
    demo_files = {str(f) for v in shots(demo) for f in files(v)}
    test_files = {Path(f).name for v in shots(test) for f in files(v)}
    words = sum(len(ex.spoken_tokens(line["say"])) for line in demo["lines"])
    print(f"       demo: {len(shots(demo))} shots, beats {sorted({v['beat'] for v in shots(demo)})}, {words} words, "
          f"{len(demo_files)} files; video test: {len(shots(test))} shots from {sorted(test_files)}; "
          f"nov10 v2: {len(shots(old))} shots, {sum(1 for v in shots(old) if 'beat' in v)} with a beat")
    return ({v["beat"] for v in shots(demo)} == set(ex.BEATS) and demo["end_pill"]["text"] == "@NeonGetaway \u2022 Subscribe"
            and shots(demo)[0]["kind"] == "video" and all("rockstar" in f for f in demo_files) and 95 <= words <= 110
            and {v["beat"] for v in shots(test)} == set(ex.BEATS) and test_files == {"test1.webm"}
            and "TEST ONLY" in test["title"] and "TEST ONLY" in test["end_pill"]["text"]
            and any(v.get("banner", {}).get("text") == "TEST ONLY" for v in shots(test))
            and all("beat" not in v for v in shots(old)) and old["end_pill"] is None and len(shots(old)) == 23)


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
