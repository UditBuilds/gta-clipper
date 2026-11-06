"""Checks for hooks.py: hooks.txt, the text and .ass writing, and clip.py --burn-hooks on a small made-up run folder.

The folder's name holds an apostrophe, brackets, a comma and a semicolon, and one hook holds emoji, Hindi, braces, a
backslash, commas and quotes. --burn-hooks runs with Python's plain Windows settings (cp1252 when output is piped).

Each check prints PASS or FAIL with what it tests. A check that raises counts as FAIL (with the error). The last line
is RESULT: PASS or FAIL; exit code 0 or 1. Needs ffmpeg and ffprobe on PATH; about 15 s.

Usage:  python hooktest.py
"""

import array
import functools
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import hooks

HERE = Path(__file__).resolve().parent
FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")
CHECKS = []
_TMP = tempfile.TemporaryDirectory()
ROOT = Path(_TMP.name)
FANCY_HOOK = 'BHAI \U0001F525 {POLICE}, "NE" \\N GHER \u092D\u093E\u0908 | \U0001F46E\u200D\u2642\uFE0F!'
GAME_HZ, VOICE_HZ = 1000, 200
VOICE_START, VOICE_END = 1.0, 3.0  # where the made-up voice file sounds


def check(fn):
    CHECKS.append(fn)
    return fn


# --- hooks.txt ---

CLIPS = [{"rank": 1, "start_s": 181.75, "end_s": 211.75, "stars_s": 30.5},
         {"rank": 2, "start_s": 153.0, "end_s": 171.75, "stars_s": 0}]


@check
def hooks_txt_has_one_line_per_clip():
    """the line format the owner asked for, with stars from stars_s"""
    return hooks.hooks_text(CLIPS) == ("clip_01 | 3:01.8\u20133:31.8 | stars: yes | hook:\n"
                                       "clip_02 | 2:33.0\u20132:51.8 | stars: no | hook:\n")


@check
def typed_hooks_come_back():
    """a typed hook, an empty one, a | inside a hook and Windows line ends all read back right"""
    text = hooks.hooks_text(CLIPS).replace("hook:\n", "hook: A | B, C\r\n", 1)
    return hooks.parse_hooks(text, ["clip_01", "clip_02"]) == {"clip_01": "A | B, C", "clip_02": ""}


@check
def notepad_encodings_are_read():
    """UTF-8 with a byte order mark and UTF-16 both give clip_01, not a clip name with a mark stuck to it"""
    text = hooks.hooks_text(CLIPS[:1]).replace("hook:", "hook: BHAI")
    return all(hooks.parse_hooks(hooks.decode_hooks(raw), ["clip_01"]) == {"clip_01": "BHAI"}
               for raw in (text.encode("utf-8-sig"), text.encode("utf-16"), text.encode("utf-8")))


def raises_hook_error(fn, *args):
    try:
        fn(*args)
    except hooks.HookError:
        return True
    return False


@check
def bad_hooks_txt_stops():
    """a line in the wrong form, an unknown clip, a clip given twice and a file that isn't UTF-8 are errors"""
    return (raises_hook_error(hooks.parse_hooks, "clip_01 BHAI\n", ["clip_01"])
            and raises_hook_error(hooks.parse_hooks, "clip_09 | t | s | hook: X\n", ["clip_01"])
            and raises_hook_error(hooks.parse_hooks, "clip_01 | t | s | hook: X\nclip_01 | t | s | hook: Y\n", ["clip_01"])
            and raises_hook_error(hooks.decode_hooks, "hook: \u2013".encode("cp1252")))


# --- Text and .ass ---

@check
def emoji_go_and_text_stays():
    """emoji, flags, keycaps and joined emoji go; the Rupee sign, Hindi and its joiner stay; spaces collapse"""
    return (hooks.clean_hook("BHAI\U0001F525POLICE") == ("BHAI POLICE", True)
            and hooks.clean_hook("\U0001F1EE\U0001F1F3 OK \U0001F46E\u200D\u2642\uFE0F") == ("OK", True)
            and hooks.clean_hook("1\uFE0F\u20E3 BHAI") == ("1 BHAI", True)
            and hooks.clean_hook("\u20B9500 \u0915\u094D\u200D\u0937") == ("\u20B9500 \u0915\u094D\u200D\u0937", False)
            and hooks.clean_hook("\U0001F525\u2764\uFE0F") == ("", True)
            and hooks.clean_hook(" A\t B\u2028C\x07 ") == ("A B C", False))


@check
def escaping_matches_what_libass_needs():
    """braces get a backslash, a backslash gets U+2060, in one pass; commas and quotes stay"""
    return (hooks.escape_ass("A{B}C") == "A\\{B\\}C"
            and hooks.escape_ass("A\\NB") == "A\\\u2060NB"
            and hooks.escape_ass("\\{") == "\\\u2060\\{"
            and hooks.escape_ass('A, "B" \'C\'; D: E') == 'A, "B" \'C\'; D: E')


@check
def ass_file_keeps_the_text_in_the_last_field():
    """Dialogue has 10 fields with commas in the hook; no fade in, 200 ms fade out, first 3 s, box centre"""
    doc = hooks.ass_document(["AAJ, KAL", '"PARSON"'], 100)
    fields = next(line for line in doc.splitlines() if line.startswith("Dialogue:")).split(",", 9)
    return (fields[1] == "0:00:00.00" and fields[2] == "0:00:03.00" and len(fields) == 10
            and fields[9] == '{\\an5\\pos(510,518)\\fad(0,200)}AAJ, KAL\\N"PARSON"'
            and "PlayResX: 1080" in doc and "PlayResY: 1920" in doc and "Hook,Impact,100," in doc)


@check
def two_lines_split_evenly():
    """the split makes the longer line as short as it can be; a single word has none"""
    return (hooks.two_lines("BHAI POLICE NE GHER LIYA!") == ["BHAI POLICE", "NE GHER LIYA!"]
            and hooks.two_lines("BHAI") is None)


# --- Drawing with ffmpeg ---

def fit_box(text):
    fit = hooks.fit_banner(FFMPEG, text)
    with tempfile.TemporaryDirectory() as folder:
        return fit, hooks.banner_box(FFMPEG, fit[0], fit[1], folder)


@check
def bigger_layout_wins_and_stays_in_the_box():
    """5 letters stay on 1 line at the top size; 25 letters take 2 lines at the top size; 80 take 2 smaller lines"""
    short, short_box = fit_box("BHAI!")
    mid, mid_box = fit_box("BHAI POLICE NE GHER LIYA!")
    long, long_box = fit_box("BHAI YE TOH POLICE WALE PURE SHEHAR MEIN PEECHE PAD GAYE AUR AB BACHNA MUSHKIL HAI")
    return (len(short[0]) == 1 and short[1] == hooks.MAX_SIZE and len(mid[0]) == 2 and mid[1] == hooks.MAX_SIZE
            and len(long[0]) == 2 and hooks.MIN_SIZE < long[1] < hooks.MAX_SIZE
            and all(hooks.inside_box(b) for b in (short_box, mid_box, long_box)))


@check
def too_long_does_not_fit():
    """a hook that can't fit at the smallest size gives no fit"""
    return hooks.fit_banner(FFMPEG, " ".join(["PAAGALPANTI"] * 30)) is None


@check
def missing_font_is_reported():
    """Impact is found here; a font that doesn't exist gives a warning"""
    saved, good = hooks.FONT, hooks.font_problem(FFMPEG)
    hooks.FONT = "NoSuchFontAtAll"
    try:
        return good is None and bool(hooks.font_problem(FFMPEG))
    finally:
        hooks.FONT = saved


@check
def voice_span_ignores_silence_at_the_ends():
    """silence at both ends is cut off, a pause between words isn't, a silent file gives None"""
    log = "silence_start: 0\nsilence_end: 1.5\nsilence_start: 2.5\nsilence_end: 2.7\nsilence_start: 5.2\nsilence_end: 7.5\n"
    return (hooks.voice_span(log, 7.5) == (1.5, 5.2) and hooks.voice_span("", 4.0) == (0.0, 4.0)
            and hooks.voice_span("silence_start: 0\nsilence_end: 4\n", 4.0) is None
            and hooks.voice_span("silence_start: 3.5\n", 4.0) == (0.0, 3.5))


@check
def duck_gain_has_the_right_shape():
    """full before, half way on each ramp, 25 % during, full after; a voice from 0 finds the game already down"""
    def gain(start, end, t):
        return eval(hooks.duck_gain(start, end), {"clip": lambda v, a, b: max(a, min(v, b)), "min": min, "t": t})
    want = [((1, 3, 0.5), 1.0), ((1, 3, 0.85), 0.625), ((1, 3, 2.0), 0.25), ((1, 3, 3.15), 0.625), ((1, 3, 3.5), 1.0),
            ((0, 3, 0.0), 0.25)]
    return all(abs(gain(*args) - value) < 1e-6 for args, value in want)


# --- clip.py --burn-hooks on a made-up run folder ---

def ff(*args, cwd=None):
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], cwd=cwd, check=True, capture_output=True)


def burn(run_dir):
    """clip.py --burn-hooks with Python's plain Windows settings: piped output is cp1252 unless told otherwise."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    env["PYTHONUTF8"] = "0"
    return subprocess.run([sys.executable, str(HERE / "clip.py"), "--burn-hooks", str(run_dir)], env=env, capture_output=True,
                          text=True, encoding="cp1252", errors="replace")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_clip(path, audio=True):
    picture = ["-f", "lavfi", "-i", "color=c=0x404040:s=1080x1920:r=30:d=6"]
    sound = ["-f", "lavfi", "-i", f"sine=frequency={GAME_HZ}:duration=6", "-ac", "2", "-c:a", "aac", "-b:a", "192k"] if audio else []
    ff(*picture, *sound, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-shortest", str(path))


def make_run(name, clips_with_audio):
    run_dir = ROOT / name
    run_dir.mkdir()
    entries = []
    for i, audio in enumerate(clips_with_audio, 1):
        entries.append({"rank": i, "file": f"clip_{i:02d}_0m00s.mp4", "start_s": 0.0, "end_s": 6.0, "stars_s": 0})
        make_clip(run_dir / entries[-1]["file"], audio)
    (run_dir / "scores.json").write_text(json.dumps({"clips": entries}), encoding="utf-8")
    return run_dir


def make_voice(path, total=4.0):
    tail = max(0.0, total - VOICE_END)
    ff("-f", "lavfi", "-i", f"sine=frequency={VOICE_HZ}:sample_rate=44100:duration={VOICE_END - VOICE_START}",
       "-af", f"adelay={int(VOICE_START * 1000)}:all=1,apad=pad_dur={tail}", str(path))


@functools.lru_cache(maxsize=None)
def burned():
    """Clip 1: hook and voice. Clip 2: nothing. Clip 3: voice only. Clip 4: hook only. Clip 5: no game audio, and a voice
    longer than the clip. Burned twice, as the owner will when changing a hook: _final files must not become inputs."""
    run_dir = make_run("Bob's [x], y=1; z", [True, True, True, True, False])
    make_voice(run_dir / "clip_01_vo.wav")
    make_voice(run_dir / "clip_03_vo.wav")
    make_voice(run_dir / "clip_05_vo.wav", total=9.0)
    first = burn(run_dir)  # no hooks.txt yet: writes it and stops
    finals_after_first = list(run_dir.glob("*final*"))
    text = hooks.hooks_text([{"rank": i, "start_s": 0.0, "end_s": 6.0} for i in range(1, 6)])
    typed = text.replace("hook:", "hook: " + FANCY_HOOK, 1).replace(
        "clip_04 | 0:00.0\u20130:06.0 | stars: no | hook:", "clip_04 | 0:00.0\u20130:06.0 | stars: no | hook: BHAI BHAG")
    (run_dir / "hooks.txt").write_bytes(typed.encode("utf-8-sig"))
    before = {p.name: sha(p) for p in run_dir.glob("clip_*_0m00s.mp4")}
    second = burn(run_dir)
    third = burn(run_dir)
    return run_dir, first, second, third, before, finals_after_first


@check
def missing_hooks_txt_is_written_and_kept():
    """no hooks.txt: it is written, nothing is burned; a typed one is never overwritten by the next run"""
    run_dir, first, _, _, _, finals_after_first = burned()
    return (first.returncode == 0 and "hook:" in first.stdout and finals_after_first == []
            and FANCY_HOOK.split()[0] in (run_dir / "hooks.txt").read_text("utf-8-sig"))


@check
def burn_survives_plain_windows_settings():
    """emoji, Hindi and an odd folder name don't crash it on piped cp1252 output, and no hook text is printed"""
    _, _, second, third, *_ = burned()
    return second.returncode == 0 and third.returncode == 0 and "POLICE" not in second.stdout


@check
def originals_are_untouched_and_finals_are_complete():
    """every original keeps its hash over two burns, with one _final per clip and no temp files left"""
    run_dir, _, _, _, before, _ = burned()
    finals = sorted(p.name for p in run_dir.glob("*.mp4") if "final" in p.name)
    return (before == {p.name: sha(p) for p in run_dir.glob("clip_*_0m00s.mp4")} and len(before) == 5
            and finals == [f"clip_{i:02d}_final.mp4" for i in range(1, 6)])


@check
def nothing_to_do_is_a_byte_exact_copy():
    """empty hook and no voice: the final file has the same bytes as the original"""
    run_dir, *_ = burned()
    return sha(run_dir / "clip_02_final.mp4") == sha(run_dir / "clip_02_0m00s.mp4")


def stream_md5(path, stream):
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", stream, "-c", "copy", "-f", "md5", "-"],
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


@check
def only_what_changes_is_reencoded():
    """voice only keeps the picture stream exactly; hook only keeps the audio stream exactly"""
    run_dir, *_ = burned()
    return (stream_md5(run_dir / "clip_03_final.mp4", "0:v:0") == stream_md5(run_dir / "clip_03_0m00s.mp4", "0:v:0")
            and stream_md5(run_dir / "clip_04_final.mp4", "0:a:0") == stream_md5(run_dir / "clip_04_0m00s.mp4", "0:a:0"))


def gray_frame(path, n):
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path), "-vf", f"select=eq(n\\,{n})", "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    return r.stdout


def changed_box(a, b, w=1080, limit=30):
    """(x1, x2, y1, y2) of the pixels that differ between two gray frames, or None."""
    rows = [y for y in range(len(a) // w) if a[y * w:(y + 1) * w] != b[y * w:(y + 1) * w]
            and any(abs(p - q) > limit for p, q in zip(a[y * w:(y + 1) * w], b[y * w:(y + 1) * w]))]
    if not rows:
        return None
    cols = [x for y in rows for x in range(w) if abs(a[y * w + x] - b[y * w + x]) > limit]
    return min(cols), max(cols), rows[0], rows[-1]


@check
def banner_is_there_from_frame_0_and_gone_after_3_s():
    """the banner is in the box on frame 0 and at 1 s, at the same place (no fade in), and gone at 3.2 s"""
    run_dir, *_ = burned()
    src, out = run_dir / "clip_01_0m00s.mp4", run_dir / "clip_01_final.mp4"
    first = changed_box(gray_frame(src, 0), gray_frame(out, 0))
    second = changed_box(gray_frame(src, 30), gray_frame(out, 30))
    late = changed_box(gray_frame(src, 96), gray_frame(out, 96))
    return first == second and hooks.inside_box(first) and late is None


def mono(path):
    r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "48000",
                        "-f", "f32le", "-"], capture_output=True, check=True)
    samples = array.array("f")
    samples.frombytes(r.stdout)
    return samples


def tone(samples, start, end, hz):
    """Strength of one frequency between two times (Goertzel)."""
    part = samples[int(start * 48000):int(end * 48000)]
    c = 2 * math.cos(2 * math.pi * hz / 48000)
    s1 = s2 = 0.0
    for x in part:
        s1, s2 = x + c * s1 - s2, s1
    return 2 * math.sqrt(max(0.0, s1 * s1 + s2 * s2 - c * s1 * s2)) / len(part)


def near(value, target, share=0.1):
    return abs(value - target) <= share * target


@check
def game_ducks_only_while_the_voice_sounds():
    """game full before the voice, 25 % during it, part way on the ramp, full again after; never lost at either end"""
    run_dir, *_ = burned()
    src, out = mono(run_dir / "clip_01_0m00s.mp4"), mono(run_dir / "clip_01_final.mp4")
    full = tone(src, 0.2, 5.5, GAME_HZ)
    ramp = tone(out, VOICE_END + 0.13, VOICE_END + 0.17, GAME_HZ)
    return (near(tone(out, 0.2, 0.6, GAME_HZ), full) and near(tone(out, VOICE_START + 0.3, VOICE_END - 0.3, GAME_HZ), full * 0.25)
            and 0.4 * full < ramp < 0.85 * full and near(tone(out, VOICE_END + 0.5, 5.5, GAME_HZ), full))


@check
def voice_is_on_top_and_on_time():
    """the voice is at least 3 times the ducked game, silent before its start, and sounding right after it"""
    run_dir, *_ = burned()
    out = mono(run_dir / "clip_01_final.mp4")
    during = tone(out, VOICE_START + 0.3, VOICE_END - 0.3, VOICE_HZ)
    return (during > 3 * tone(out, VOICE_START + 0.3, VOICE_END - 0.3, GAME_HZ)
            and tone(out, 0.2, VOICE_START - 0.05, VOICE_HZ) < 0.05 * during
            and tone(out, VOICE_START + 0.03, VOICE_START + 0.2, VOICE_HZ) > 0.5 * during)


@check
def clip_without_game_audio_gets_the_voice_alone():
    """no audio stream in the clip: the voice is the audio, cut to the clip's length, and the clip stays 6 s long"""
    run_dir, *_ = burned()
    seconds, has_audio = hooks.probe(FFPROBE, "clip_05_final.mp4", run_dir)
    out = mono(run_dir / "clip_05_final.mp4")
    return has_audio and abs(seconds - 6.0) < 0.1 and tone(out, 1.3, 2.7, VOICE_HZ) > 0.05


@check
def too_long_hook_stops_before_any_encode():
    """a hook that doesn't fit stops with an error naming the clip, and no _final file is written"""
    run_dir = make_run("too long", [True])
    (run_dir / "hooks.txt").write_text("clip_01 | t | stars: no | hook: " + " ".join(["PAAGALPANTI"] * 30) + "\n", encoding="utf-8")
    r = burn(run_dir)
    return r.returncode == 1 and "clip_01" in r.stderr and not list(run_dir.glob("*final*"))


@check
def an_open_final_and_stray_voice_files_are_explained():
    """a _final file a player holds open is a plain error, not a crash; a .mp3 voice and a voice for no clip get warnings"""
    run_dir = make_run("player", [True])
    (run_dir / "hooks.txt").write_text("clip_01 | t | stars: no | hook:\n", encoding="utf-8")
    (run_dir / "clip_01_vo.mp3").write_bytes(b"")
    (run_dir / "clip_09_vo.wav").write_bytes(b"")
    first = burn(run_dir)
    with open(run_dir / "clip_01_final.mp4", "rb"):
        locked = burn(run_dir)
    return (first.returncode == 0 and first.stdout.count("WARNING") == 2 and locked.returncode == 1
            and "open in another program" in locked.stderr and "Traceback" not in locked.stderr
            and not list(run_dir.glob("*.tmp.mp4")))


@check
def a_folder_with_no_scores_stops():
    """--burn-hooks on a folder with no scores.json is a clear error, and a normal run still needs a video"""
    (ROOT / "empty").mkdir()
    no_video = subprocess.run([sys.executable, str(HERE / "clip.py")], capture_output=True, text=True)
    return burn(ROOT / "empty").returncode == 1 and no_video.returncode == 2 and "video" in no_video.stderr


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
    _TMP.cleanup()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
