"""Hook banners and voiceovers for finished clips (clip.py --burn-hooks). Python standard library + ffmpeg only.

A normal clip.py run writes hooks.txt into its run folder, one line per clip:
    clip_01 | 3:01.8–3:31.8 | stars: yes | hook:
The owner types a hook after "hook:" (empty = no banner) and runs clip.py --burn-hooks <run folder>, which writes
clip_01_final.mp4 ... next to the clips. No detection runs and nothing is picked; the original clips are only read.

Banner: FONT in white with a black outline, inside BOX, in full from the clip's first frame, for BANNER_END with a
FADE_OUT_MS fade out. The size is found by drawing the text and reading the pixel box (ffmpeg's bbox filter, outline
included): the largest size that stays inside BOX, on 1 line or 2, whichever is bigger.
Voice: a file clip_01_vo.m4a or clip_01_vo.wav is normalised and mixed in. The game drops to DUCK_GAIN only while the
voice sounds, with a DUCK_RAMP_S ramp, and is back at full volume after it.
Only what changes is re-encoded: no hook and no voice is a byte-for-byte copy, a hook alone copies the audio, a voice
alone copies the picture.

Three things here are for Windows, all tested on it. ffmpeg runs inside the run folder and gets plain file names, as a
path in the ass filter needs two levels of escaping (a run folder with an apostrophe or a bracket breaks anything less).
Hook text is never printed: with piped output Python uses cp1252 and an emoji would crash the run. hooks.txt may come
back from Notepad with a byte order mark, which would otherwise end up inside "clip_01".
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

HOOKS_FILE = "hooks.txt"
FRAME_W, FRAME_H = 1080, 1920
# Where the banner may draw (inclusive, frame pixels). The Shorts dead zones are top 380, bottom 380, right 120 and
# left 60. The full-frame layout's game picture is rows 656-1263, so this is the blurred band above it. With --crop
# the same box lies over the game: that layout has no empty band.
BOX_LEFT, BOX_RIGHT, BOX_TOP, BOX_BOTTOM = 60, 959, 380, 655
BOX_CENTRE = (510, 518)  # x is 30 px left of the frame's centre because the left and right dead zones differ
FONT = "Impact"          # one weight only, so Bold stays 0: bold would ask libass for a faked one
MAX_SIZE = 140           # two lines of this just fit the box's 276 rows
MIN_SIZE = 40            # smaller is hard to read on a phone, so burning stops and asks for a shorter hook
OUTLINE_SHARE = 0.09     # outline width as a share of the font size
BANNER_END = "0:00:03.00"
FADE_OUT_MS = 200
DUCK_GAIN = 0.25         # the game's volume while the voice sounds
DUCK_RAMP_S = 0.3
VOICE_LOUDNORM = "loudnorm=I=-14:TP=-3:LRA=11"  # peaks of 0.71 + game of 0.25 can't clip, so no limiter touches the game
VOICE_SILENCE = "silencedetect=noise=-45dB:d=0.1"
VOICE_EXTS = (".m4a", ".wav")  # the first one wins if both are there

_EMOJI_CORE = "\U0001F000-\U0001FAFF\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF\U000E0020-\U000E007F"
_EMOJI = re.compile(f"[{_EMOJI_CORE}](?:[{_EMOJI_CORE}\uFE0E\uFE0F\u20E3]|\u200D)*")
_EMOJI_LEFTOVER = re.compile("[\uFE0E\uFE0F\u20E3]")  # the keycap in 1\uFE0F\u20E3 leaves the 1


class HookError(Exception):
    """The run folder or hooks.txt can't be used: clip.py stops before it encodes anything."""


def hms(seconds):
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:04.1f}"


def clip_id(rank):
    return f"clip_{rank:02d}"


# --- hooks.txt ---

def hooks_text(clips):
    return "".join(f"{clip_id(c['rank'])} | {hms(c['start_s'])}\u2013{hms(c['end_s'])} | "
                   f"stars: {'yes' if c.get('stars_s', 0) > 0 else 'no'} | hook:\n" for c in clips)


def write_hooks_txt(run_dir, clips):
    (run_dir / HOOKS_FILE).write_text(hooks_text(clips), encoding="utf-8")


def decode_hooks(raw):
    try:
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):  # Notepad's "UTF-16"
            return raw.decode("utf-16")
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HookError(f"{HOOKS_FILE} is not UTF-8. In Notepad: File, Save as, Encoding UTF-8.") from None


def parse_hooks(text, known):
    """{clip id: hook as typed} from the text of hooks.txt; known is the clip ids of the run. Only the clip id and what
    follows "hook:" are read, and the hook itself may contain "|"."""
    hooks = {}
    for n, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        fields = [f.strip() for f in line.split("|", 3)]
        if len(fields) != 4 or not fields[3].lower().startswith("hook:"):
            raise HookError(f"{HOOKS_FILE} line {n}: expected: clip_01 | time | stars: yes | hook: your text")
        cid = fields[0]
        if cid not in known:
            raise HookError(f"{HOOKS_FILE} line {n}: this run has no clip named {ascii(cid)}")
        if cid in hooks:
            raise HookError(f"{HOOKS_FILE} line {n}: {cid} is there twice")
        hooks[cid] = fields[3][len("hook:"):].strip()
    return hooks


# --- Hook text ---

def clean_hook(raw):
    """(text, emoji removed?). libass can only draw emoji as one-colour outlines, and some come out wrong (a flag as two
    letters), so they go. Control characters go too and spaces collapse. A ZWJ stays unless it joins emoji, as Hindi
    uses it."""
    text, count = _EMOJI.subn(" ", raw)
    text, leftover = _EMOJI_LEFTOVER.subn("", text)
    kept = []
    for ch in text:
        kind = unicodedata.category(ch)
        if kind in ("Cc", "Zl", "Zp"):
            kept.append(" ")
        elif kind != "Cf" or ch in "\u200C\u200D":
            kept.append(ch)
    return " ".join("".join(kept).split()), bool(count or leftover)


def escape_ass(text):
    """Text that libass draws exactly as typed. { starts a style command and \\N, \\n and \\h are commands, in one pass
    so the fixes can't touch each other. Doubling a backslash does not escape it: libass still sees the \\N after it, so
    an invisible U+2060 goes between them. Commas, quotes, ; and : need nothing: Text is the last field of the line."""
    return re.sub(r"[\\{}]", lambda m: "\\\u2060" if m.group() == "\\" else "\\" + m.group(), text)


def two_lines(text):
    """The text split into 2 lines at the space that makes the longer line shortest, or None for a single word."""
    words = text.split(" ")
    if len(words) < 2:
        return None
    k = min(range(1, len(words)), key=lambda i: max(len(" ".join(words[:i])), len(" ".join(words[i:]))))
    return [" ".join(words[:k]), " ".join(words[k:])]


def ass_document(lines, size, measuring=False):
    """A whole .ass file for the banner; lines is the text as typed. While measuring the outline is white and there is
    no fade, so bbox sees every pixel the banner can cover on the first frame."""
    outline_colour = "&H00FFFFFF" if measuring else "&H00000000"
    fade = "" if measuring else f"\\fad(0,{FADE_OUT_MS})"
    text = "\\N".join(escape_ass(line) for line in lines)
    return (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {FRAME_W}\nPlayResY: {FRAME_H}\nWrapStyle: 2\n"
            "ScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding\n"
            f"Style: Hook,{FONT},{size},&H00FFFFFF,&H000000FF,{outline_colour},&H00000000,0,0,0,0,100,100,0,0,1,"
            f"{round(size * OUTLINE_SHARE)},0,5,0,0,0,1\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            f"Dialogue: 0,0:00:00.00,{BANNER_END},Hook,,0,0,0,,"
            f"{{\\an5\\pos({BOX_CENTRE[0]},{BOX_CENTRE[1]}){fade}}}{text}\n")


# --- Fitting the banner ---

def run_ffmpeg(ffmpeg, args, cwd, loglevel="error"):
    return subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-loglevel", loglevel, *args], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def draw_once(ffmpeg, lines, size, folder, loglevel="info"):
    """ffmpeg's log of drawing the banner once on a black frame, for bbox to read."""
    (Path(folder) / "measure.ass").write_bytes(ass_document(lines, size, measuring=True).encode("utf-8"))
    return run_ffmpeg(ffmpeg, ["-f", "lavfi", "-i", f"color=black:s={FRAME_W}x{FRAME_H}:r=25:d=0.04", "-frames:v", "1",
                               "-vf", "ass=measure.ass,bbox=min_val=24", "-f", "null", "-"], folder, loglevel).stderr


def banner_box(ffmpeg, lines, size, folder):
    """(x1, x2, y1, y2) of the pixels the banner covers, outline included, or None if it draws nothing."""
    m = re.search(r"x1:(\d+) x2:(\d+) y1:(\d+) y2:(\d+)", draw_once(ffmpeg, lines, size, folder))
    return tuple(int(v) for v in m.groups()) if m else None


def inside_box(box):
    return bool(box) and box[0] >= BOX_LEFT and box[1] <= BOX_RIGHT and box[2] >= BOX_TOP and box[3] <= BOX_BOTTOM


def largest_size(ffmpeg, lines, folder):
    """The largest size from MIN_SIZE to MAX_SIZE that stays inside the box, or None. A text wider than the frame is
    cut at its edge, which is still outside the box, so a size can't pass by being cut."""
    low, high, best = MIN_SIZE, MAX_SIZE, None
    while low <= high:
        mid = (low + high) // 2
        if inside_box(banner_box(ffmpeg, lines, mid, folder)):
            best, low = mid, mid + 1
        else:
            high = mid - 1
    return best


def fit_banner(ffmpeg, text):
    """(lines, size) with the biggest size, on 1 line or 2 (1 line when they tie), or None if neither fits at MIN_SIZE."""
    split = two_lines(text)
    layouts = [[text], split] if split else [[text]]
    with tempfile.TemporaryDirectory() as folder:
        sized = [(largest_size(ffmpeg, lines, folder), lines) for lines in layouts]
    sized = [(size, lines) for size, lines in sized if size]
    if not sized:
        return None
    size, lines = max(sized, key=lambda s: s[0])  # max keeps the first of a tie
    return lines, size


def font_problem(ffmpeg):
    """A warning if libass doesn't find FONT: it would quietly use another. The fit still holds, as it measures what is
    drawn."""
    with tempfile.TemporaryDirectory() as folder:
        log = draw_once(ffmpeg, ["TEST"], MIN_SIZE, folder, loglevel="debug")
    m = re.search(r"fontselect: \(.*?\) -> ([^,\r\n]+)", log)
    if m and m.group(1).strip() != FONT:
        return f"font {FONT} not found, the banners use {m.group(1).strip()} instead"
    return None


# --- Voice ---

def find_voice(run_dir, cid):
    found = [f"{cid}_vo{ext}" for ext in VOICE_EXTS if (run_dir / f"{cid}_vo{ext}").is_file()]
    if len(found) > 1:
        print(f"WARNING: {cid}: more than one voice file here, using {found[0]}")
    return found[0] if found else None


def voice_span(log, duration):
    """(start, end) of the sound in a voice file from silencedetect's log, or None if it is silent throughout. Silence
    at the file's two ends doesn't count; a pause between words does, so the game doesn't pump up and down."""
    starts = [float(v) for v in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(v) for v in re.findall(r"silence_end: ([\d.]+)", log)]
    start, end = 0.0, duration
    if starts and starts[0] <= 0.01 and ends:
        start = ends[0]
    if starts and (len(ends) < len(starts) or ends[-1] >= duration - 0.05):
        end = starts[-1]
    return (start, end) if end > start else None


def duck_gain(start, end):
    """ffmpeg volume expression: 1 before and after, DUCK_GAIN from start to end, a straight ramp on each side. A voice
    that starts at 0 finds the game already down."""
    r = DUCK_RAMP_S
    return f"1-{1 - DUCK_GAIN:g}*clip(min((t-({start - r:.3f}))/{r:g},({end + r:.3f}-t)/{r:g}),0,1)"


def audio_graph(span, has_game, clip_s):
    """filter_complex for the voice mix, with its output [a]. amix's normalize=0 matters: by default it halves both."""
    voice = f"[1:a]aformat=channel_layouts=stereo,{VOICE_LOUDNORM},aresample=48000"
    if not has_game:
        return f"{voice},atrim=end={clip_s:.3f}[a]"
    return (f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,asetnsamples=n=240:p=0,"
            f"volume='{duck_gain(*span)}':eval=frame[game];{voice}[voice];"
            f"[game][voice]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")


# --- Burning a run folder ---

def probe(ffprobe, name, cwd):
    """(duration in seconds, has an audio stream) of a file in cwd."""
    r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", name],
                       cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise HookError(f"{name}: ffprobe can't read it")
    info = json.loads(r.stdout)
    return float(info.get("format", {}).get("duration", 0)), any(s.get("codec_type") == "audio"
                                                                 for s in info.get("streams", []))


def load_clips(run_dir):
    if not (run_dir / "scores.json").is_file():
        raise HookError(f"no scores.json in {run_dir}: give the folder of one run, the one with the clips in it")
    clips = json.loads((run_dir / "scores.json").read_text(encoding="utf-8"))["clips"]
    for c in clips:
        if not (run_dir / c["file"]).is_file():
            raise HookError(f"{c['file']} is listed in scores.json but is not in {run_dir}")
    return clips


def plan_clip(clip, raw_hook, run_dir, ffmpeg, ffprobe):
    """What to do for one clip, checked but not encoded yet. Prints one line about it; never the hook text."""
    cid = clip_id(clip["rank"])
    text, had_emoji = clean_hook(raw_hook)
    plan = {"cid": cid, "source": clip["file"], "out": f"{cid}_final.mp4", "lines": None, "size": None, "voice": None,
            "span": None}
    plan["clip_s"], plan["has_game"] = probe(ffprobe, clip["file"], run_dir)
    notes = []
    if had_emoji:
        notes.append("emoji removed from the hook")
    if text:
        fit = fit_banner(ffmpeg, text)
        if fit is None:
            raise HookError(f"{cid}: the hook is too long, it doesn't fit at font size {MIN_SIZE}. Please shorten it.")
        plan["lines"], plan["size"] = fit
        notes.append(f"banner size {plan['size']} on {len(plan['lines'])} line(s)")
    else:
        notes.append("no banner")
    plan["voice"] = find_voice(run_dir, cid)
    if plan["voice"]:
        duration, has_audio = probe(ffprobe, plan["voice"], run_dir)
        if not has_audio or duration <= 0:
            raise HookError(f"{plan['voice']}: there is no audio in it")
        log = run_ffmpeg(ffmpeg, ["-i", plan["voice"], "-map", "0:a:0", "-af", VOICE_SILENCE, "-f", "null", "-"], run_dir,
                         "info").stderr
        plan["span"] = voice_span(log, duration)
        if plan["span"] is None:
            raise HookError(f"{plan['voice']}: it is silent")
        start, end = plan["span"]
        notes.append(f"voice {plan['voice']} sounds {start:.1f}-{end:.1f} s, "
                     + (f"game at {DUCK_GAIN:.0%} only then" if plan["has_game"] else "this clip has no game audio"))
        if end > plan["clip_s"]:
            notes.append(f"WARNING: the voice is longer than the {plan['clip_s']:.1f} s clip and gets cut")
    if not plan["lines"] and not plan["voice"]:
        notes.append("no voice: exact copy")
    print(f"  {cid}: " + "; ".join(notes))
    return plan


def burn_clip(plan, run_dir, ffmpeg, enc_args):
    """Write plan["out"] through a temporary file, so a failed encode leaves no half-written final clip."""
    tmp = run_dir / f"{plan['cid']}_final.tmp.mp4"
    if not plan["lines"] and not plan["voice"]:
        shutil.copyfile(run_dir / plan["source"], tmp)
    else:
        args = ["-y", "-i", plan["source"]]
        graphs, maps = [], []
        if plan["voice"]:
            args += ["-i", plan["voice"]]
        if plan["lines"]:
            ass = f"{plan['cid']}_hook.ass"
            (run_dir / ass).write_bytes(ass_document(plan["lines"], plan["size"]).encode("utf-8"))
            graphs.append(f"[0:v]ass={ass}[v]")
            maps += ["-map", "[v]"] + enc_args
        else:
            maps += ["-map", "0:v:0", "-c:v", "copy"]
        if plan["voice"]:
            graphs.append(audio_graph(plan["span"], plan["has_game"], plan["clip_s"]))
            maps += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
        elif plan["has_game"]:
            maps += ["-map", "0:a:0", "-c:a", "copy"]
        if graphs:
            args += ["-filter_complex", ";".join(graphs)]
        r = run_ffmpeg(ffmpeg, args + maps + ["-movflags", "+faststart", tmp.name], run_dir)
        if r.returncode != 0:
            tmp.unlink(missing_ok=True)
            tail = "\n".join(r.stderr.strip().splitlines()[-15:])
            raise HookError(f"{plan['cid']}: ffmpeg failed ({r.returncode})\n{tail}")
    try:
        os.replace(tmp, run_dir / plan["out"])
    except PermissionError:  # Windows won't replace a file a player has open
        tmp.unlink(missing_ok=True)
        raise HookError(f"{plan['out']} is open in another program. Close it and run this command again.") from None


def burn_run(run_dir, ffmpeg, ffprobe, encoder, enc_args):
    """clip.py --burn-hooks: everything is checked first, so a bad hook stops the run before any clip is encoded."""
    sys.stdout.reconfigure(errors="backslashreplace")  # a run folder's name can hold what cp1252 can't print
    run_dir = run_dir.resolve()
    clips = load_clips(run_dir)
    hooks_path = run_dir / HOOKS_FILE
    if not hooks_path.exists():
        write_hooks_txt(run_dir, clips)
        print(f"Wrote {hooks_path}\nType your hooks after \"hook:\", save it, and run this command again.")
        return
    known = [clip_id(c["rank"]) for c in clips]
    hooks = parse_hooks(decode_hooks(hooks_path.read_bytes()), known)
    for extra in sorted(p.name for p in run_dir.glob("clip_*_vo.*")):
        cid, ext = extra.rsplit("_vo", 1)
        if cid not in known or ext.lower() not in VOICE_EXTS:
            print(f"WARNING: {extra} is not used: a voice file is named clip_01_vo.m4a or clip_01_vo.wav, "
                  "for a clip of this run")
    if any(hooks.values()):
        problem = font_problem(ffmpeg)
        if problem:
            print("WARNING: " + problem)
    print(f"Checking {len(clips)} clip(s):")
    plans = [plan_clip(c, hooks.get(clip_id(c["rank"]), ""), run_dir, ffmpeg, ffprobe) for c in clips]
    print(f"Encoder: {encoder}. Writing {len(plans)} file(s) to {run_dir}")
    for plan in plans:
        burn_clip(plan, run_dir, ffmpeg, enc_args)
        print(f"  {plan['out']}")
