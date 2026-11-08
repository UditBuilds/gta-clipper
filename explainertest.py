"""Checks for explainer.py: the script checks, word timing, captions, the timeline, Ken Burns, focus, pans, card marks
and short clips, music picking, ducking and sound effects on small made-up files, then real renders of short scripts
using ffmpeg-made test videos, test images and test-tone music.

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
    # A grey ramp, black at the left edge to white at the right, so a frame's mean brightness says where a pan is.
    run_ffmpeg("-f", "lavfi", "-i", "color=black:s=1920x1080:d=0.04", "-vf",
               "format=rgb24,geq=r='X*255/(W-1)':g='X*255/(W-1)':b='X*255/(W-1)'", "-frames:v", "1", ROOT / "ramp.png")
    # A picture narrower than the screen, which can't pan.
    run_ffmpeg("-f", "lavfi", "-i", f"color=c={hexcolour(AMBER)}:s=800x1600:d=0.04", "-frames:v", "1", ROOT / "tall.png")
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
    """a voice faster than 1.1 warns (1.1 itself doesn't); a Short over 50 s warns, 40-50 s and under 40 s don't"""
    fast = ex.script_warnings({"speed": 1.18, "lines": []})
    edge = ex.script_warnings({"speed": 1.1, "lines": []})
    return (len(fast) == 1 and "faster than 1.1" in fast[0] and not edge and ex.runtime_warnings(50.5)
            and "over" in ex.runtime_warnings(50.5)[0] and not ex.runtime_warnings(45) and not ex.runtime_warnings(37.6)
            and "under" in ex.runtime_note(37.6) and "inside" in ex.runtime_note(45) and "over" in ex.runtime_note(51))


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
    titles = ["EVERY PLACE IN GTA 6", "WWWWWWWWWWWWW", "THE LONGEST WAIT IN THE WHOLE SERIES", "13 YEARS",
              "~NOV 12~|*NOV 10*", "*NOV 10*|12 AM LOCAL", "~WWWWWWWWWWWWWWWW~", "MMMM|~WWWWWWWWWWWW~|*MMMMMMMMMMMMM*"]
    events = [ex.dialogue(2, i, i + 1, "Caption", ex.caption_text([fake_word(w) for w in c])) for i, c in enumerate(captions)]
    at = len(events)
    for y in (ex.OPEN_CARD_Y, ex.CARD_Y):
        for t in titles:
            events += ex.title_events(1, at, at + 1, t, y, ex.POP)
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


GREY = 128


def ink_boxes(events, count, name):
    """Each event drawn over mid grey, one a second from second 0, and the box (x1, x2, y1, y2) around everything that
    isn't grey half a second in, after the pop: the fill, the black outline and the shadow."""
    folder = ROOT / name
    folder.mkdir(exist_ok=True)
    (folder / "t.ass").write_text(ex.ass_document(events), encoding="utf-8")
    shutil.copy(ex.FONT_FILE, folder)
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
    """drawn over grey, so the black outline and the shadow count too: the widest captions stay in Shorts' safe area, and a two-line caption's top sits below the bottom of the tallest card, open card and crossed-out bar"""
    captions = [["WWWWWWWWWWWWWWWW"], ["MMMM", "WWWW", "MMMM", "WWWW"], ["CONGRATULATIONS,", "EVERYBODY"],
                ["ABSOLUTELY-UNBELIEVABLE-THING"], ["GALLERY", "ALREADY", "SHOWS", "YOU"]]
    tall = ["MMMM|~WWWWWWWWWWWW~|*MMMMMMMMMMMMM*", "~WWWWWWWWWWWWWWWW~|MMMM|WWWW", "THE LONGEST WAIT IN THE WHOLE SERIES"]
    events = [ex.dialogue(2, i, i + 1, "Caption", ex.caption_text([fake_word(w) for w in c])) for i, c in enumerate(captions)]
    at = len(events)
    for y in (ex.OPEN_CARD_Y, ex.CARD_Y):
        for t in tall:
            events += ex.title_events(1, at, at + 1, t, y, ex.POP)
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
    return count_near(left, (0, 229, 255)) > W * H * 0.5 and count_near(right, (255, 255, 255)) > W * H * 0.5


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
            and count_near(edge, (0, 229, 255)) > W * H * 0.5)


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
    shutil.copy(ex.FONT_FILE, folder)
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
                          music=None, lines=[
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
    """the .ass has 212 GIGABYTES in neon green and DANGER in red, and untagged words have no colour of their own"""
    text = (main_run()[0] / "captions.ass").read_text(encoding="utf-8")
    white_line = next(line for line in text.splitlines() if "THE FIRST" in plain(line))
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
            and log["durations"]["target_s"] == [40, 50] and log["shots"]["count"] == len(log["visuals"])
            and re.search(r"Runtime \d+\.\d s \(target 40-50 s", stdout)
            and re.search(rf"Shots: {len(log['visuals'])} \({len(log['visuals']) - 1} plus the 1.5 s loop ending\), average", stdout)
            and {"voice_lufs", "final_lufs"} <= set(log["loudness"]))


@functools.cache
def pacing_run():
    """A script with this PR's shots: a pan to open (so the loop ending slides back), a crossed-out card, a weighted
    video with a focus, and an image with [across, down]."""
    assets()
    script = write_script("pacing.json", title="Pacing test", lines=[
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
    red = int(((bar[..., 0] > 190) & (bar[..., 1] < 100) & (bar[..., 2] < 100))[500:700].sum())
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
    """in the .ass every untagged word turns yellow at its own start (from the log's word times, to the ms) and back when the next starts, tagged words keep their colour; on screen the yellow moves from word to word inside one chunk"""
    folder, log, _ = main_run()
    words = log["word_times"]
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
        if word != timed["text"] or abs(on - round((timed["start_s"] - start) * 1000)) > 1:
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
    print(f"       {untagged} untagged words, {len(late)} off their time {late[:5]}; on screen: yellow px {lit} for "
          f"{words[chunk + 1]['text']} then {words[chunk + 2]['text']}, {overlap} px yellow in both")
    return (len(found) == len(words) and untagged >= 15 and not late and tagged_plain and min(lit) > 1500
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
