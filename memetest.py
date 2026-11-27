"""Checks for meme mode (meme_short.py, PR 39): rules 1-8 of the owner's brief of Nov 27, each with specs that must pass
and specs that must fail, and tiny real renders whose first and last frames are measured on the pixels (the caption's
place and its swap, the letterbox bars, the punch-in's zoom, the replay's slow motion, the loudness and the true peak).
No Ollama, no Whisper, no footage: the renders use made-up 16:9 videos.

  .venv\\Scripts\\python.exe memetest.py
"""
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import meme_short
import safety
import testkit

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def spec(**kw):
    """A gag that keeps every rule: 9 s, motion at 0.5 s, the punchline at 4 s, a reaction to 8 s, a hard cut at 9 s."""
    s = {"recording": "made_up", "from_s": 100.0, "to_s": 109.0, "kind": "gag", "motion_s": 100.5, "punchline_s": 104.0,
         "reaction_end_s": 108.0, "layout": "full", "focus": [0.5, 0.5],
         "captions": ["Didn't know Jason was this strong", "Jason became a gorilla for a sec", "Just another day for Jason"],
         "star": 0, "title": "Jason is way too strong"}
    s.update(kw)
    return s


def talk(**kw):
    """A dialogue Short that keeps every rule: 20 s, the first line at 0.3 s, the payoff at 17 s, the last line to 18.5 s."""
    s = spec(kind="dialogue", to_s=119.5, motion_s=None, punchline_s=117.0, reaction_end_s=118.5,
             heard=[{"from_s": 100.3, "to_s": 102.0, "text": "Hey, move your car."},
                    {"from_s": 116.5, "to_s": 118.5, "text": "Unbelievable."}])
    s.update(kw)
    return s


def rule(n, s):
    return [p for p in meme_short.problems(s) if p.startswith(f"rule {n}:")]


# --- Rule 1: length ---

@check
def a_gag_lasts_7_to_12_s():
    """Rule 1: a physics or sight gag is 7-12 s long; 6.5 s and 12.5 s are refused"""
    ok = not meme_short.problems(spec())
    short = rule(1, spec(to_s=106.5, reaction_end_s=105.5))
    long = rule(1, spec(to_s=112.5, reaction_end_s=111.5))
    seven = rule(1, spec(to_s=107.0, reaction_end_s=106.0))
    return ok and short and long and not seven, (meme_short.problems(spec()), short, long, seven)


@check
def dialogue_runs_15_to_24_s_and_nothing_passes_30():
    """Rule 1: continuous dialogue that builds to one payoff is 15-24 s; 13 s and 25 s are refused, 31 s is over the
    hard cap, and a kind that is neither is refused"""
    ok = not meme_short.problems(talk())
    short = rule(1, talk(to_s=113.0, punchline_s=111.0, reaction_end_s=112.0,
                         heard=[{"from_s": 100.3, "to_s": 112.0, "text": "a"}]))
    long = rule(1, talk(to_s=125.0, punchline_s=123.0, reaction_end_s=124.0))
    over = rule(1, talk(to_s=131.0, punchline_s=129.0, reaction_end_s=130.0))
    odd = rule(1, spec(kind="montage"))
    return (ok and short and long and any("30" in p for p in over) and odd,
            (meme_short.problems(talk()), short, long, over, odd))


# --- Rule 2: the first second ---

@check
def motion_starts_by_1_s_or_the_first_line_by_0_5_s():
    """Rule 2: motion or an impact by 1.0 s, or the first spoken line by 0.5 s; motion at 1.2 s with no early line is
    refused, a line at 0.4 s saves it, a line at 0.7 s doesn't"""
    late = rule(2, spec(motion_s=101.2))
    saved = rule(2, spec(motion_s=101.2, heard=[{"from_s": 100.4, "to_s": 101.0, "text": "Yo"}]))
    not_saved = rule(2, spec(motion_s=101.2, heard=[{"from_s": 100.7, "to_s": 101.0, "text": "Yo"}]))
    nothing = rule(2, spec(motion_s=None))
    talk_late = rule(2, talk(heard=[{"from_s": 100.7, "to_s": 102.0, "text": "Hey"}]))
    return (late and not saved and not_saved and nothing and talk_late and not rule(2, spec()),
            (late, saved, not_saved, nothing, talk_late))


@check
def a_gag_lands_its_punchline_at_2_to_6_s():
    """Rule 2: a gag's punchline lands at 0:02-0:06 (1.5 s and 6.5 s are refused); dialogue is not held to it"""
    early = rule(2, spec(punchline_s=101.5))
    late = rule(2, spec(punchline_s=106.5, reaction_end_s=108.0))
    edge = rule(2, spec(punchline_s=106.0, reaction_end_s=108.0))
    return early and late and not edge and not rule(2, talk()), (early, late, edge)


# --- Rule 3: the caption ---

@check
def a_caption_has_2_to_6_words_or_10_for_a_tried_setup():
    """Rule 3: 2-6 words; up to 10 only for "<Subject> tried <X> and this happens..."; 1, 7 and 11 words are refused"""
    c = meme_short.caption_problems
    good = ["I'm sorry Lucia", "Bro is a missile now", "Jason tried parking and this happens...",
            "Jason tried to park on a hill and this happens..."]
    bad = ["Wow", "Jason really did not see that car coming", "Jason tried to park his truck on a hill and this happens...",
           "This is a very long caption that is not a setup"]
    got = {t: c(t) for t in good + bad}
    return all(not got[t] for t in good) and all(got[t] for t in bad), got


@check
def a_caption_never_says_watch_the_or_can_you():
    """Rule 3: never "Watch the X" or "Can you X?" (in any case, the swap too)"""
    bad = ["Watch the cop", "watch this guy", "Can you stop him?", "can you believe this"]
    got = {t: meme_short.caption_problems(t) for t in bad}
    swap = meme_short.problems(spec(swap={"text": "Watch the end", "at_s": 104.5}))
    return all(got.values()) and any(p.startswith("rule 3:") for p in swap), (got, swap)


@check
def swears_are_censored_in_captions_and_titles():
    """Rule 3: censor() writes fkng and sht; a caption or title with the plain word, or with asterisks, is refused; what
    censor() writes passes the swear check the rest of the repo uses (safety.PROFANITY)"""
    c = meme_short.censor
    pairs = {"This is fucking crazy": "This is fkng crazy", "Holy shit": "Holy sht", "FUCK this": "FK this",
             "What the fuck bro": "What the fk bro", "Bullshit move": "Bullsht move"}
    got = {k: c(k) for k in pairs}
    clean = all(not safety.profanity(v) for v in got.values())
    refused = [meme_short.caption_problems("This is fucking crazy"), meme_short.caption_problems("Holy f**k bro"),
               meme_short.title_problems("Jason said shit"), meme_short.title_problems("What the f*ck")]
    passes = not meme_short.caption_problems("This is fkng crazy") and not meme_short.title_problems("Holy sht Jason")
    return got == pairs and clean and all(refused) and passes, (got, refused)


@check
def no_emoji_in_v1_so_never_an_empty_box():
    """Rule 3: v1 draws no emoji (no colour-emoji renderer without a new package), so a caption, swap or title with one,
    or with any character outside the plain set, is refused before anything renders"""
    bad = ["Bro is a rocket \U0001F680", "Jason \U0001F602", "sorry \u2764", "too strong \u2714"]
    got = {t: meme_short.caption_problems(t) for t in bad}
    title = meme_short.title_problems("Jason is too strong \U0001F480")
    return all(got.values()) and title and not meme_short.caption_problems("It's not my fault bro"), (got, title)


@check
def three_caption_options_with_one_starred_and_no_names_or_story():
    """Rule 3: exactly 3 options and a star that picks one; every option keeps the caption rules; a trailer name (only
    Jason and Lucia may be named) or a story word is refused"""
    two = rule(3, spec(captions=["I'm sorry Lucia", "Bro is a missile now"]))
    star = rule(3, spec(star=3))
    name = rule(3, spec(captions=["I'm sorry Cal", "Bro is a missile now", "Just another day for Jason"]))
    story = rule(3, spec(captions=["Jason skipped the mission", "Bro is a missile now", "Just another day for Jason"]))
    used = meme_short.caption_of(spec(star=1))
    return two and star and name and story and used == "Jason became a gorilla for a sec", (two, star, name, story, used)


@check
def the_caption_shows_from_0_to_the_last_frame_with_one_swap_after_the_payoff():
    """Rule 3: one caption from 0.00 s to past the last frame, never toggled; one optional swap after the punchline, at the
    same timestamp on both sides; a swap before the payoff, or a list of swaps, is refused"""
    plain = meme_short.caption_events(spec())
    swapped = meme_short.caption_events(spec(swap={"text": "THAT strong", "at_s": 104.6}))
    d = meme_short.timeline(spec())["duration_s"]
    one = len(plain) == 1 and plain[0][0] == 0.0 and plain[0][1] > d
    two = (len(swapped) == 2 and swapped[0][0] == 0.0 and swapped[0][1] == swapped[1][0] == 4.6
           and swapped[1][1] > d and swapped[1][2] == "THAT strong")
    early = rule(3, spec(swap={"text": "THAT strong", "at_s": 103.0}))
    many = rule(3, spec(swap=[{"text": "THAT", "at_s": 104.5}, {"text": "strong", "at_s": 105.0}]))
    ass = meme_short.ass_document(spec(swap={"text": "THAT strong", "at_s": 104.6}))
    same = ass.count("0:00:04.60") == 2
    return one and two and early and many and same, (plain, swapped, early, many)


@check
def the_caption_is_white_bold_sans_with_a_thin_black_outline_and_no_box():
    """Rule 3: the ASS style is a bold sans (Arial Bold), white, a 3-4 px black outline, no shadow and no box
    (BorderStyle 1, not 3), on a 1080x1920 canvas where a pixel is a pixel"""
    ass = meme_short.ass_document(spec())
    style = next(line for line in ass.splitlines() if line.startswith("Style: Meme,"))
    f = style[len("Style: "):].split(",")
    # Name, Font, Size, Primary, Secondary, Outline, Back, Bold, Italic, Underline, Strike, ScaleX, ScaleY, Spacing,
    # Angle, BorderStyle, Outline, Shadow, Alignment, ...
    return (f[1] == "Arial" and f[3] == "&H00FFFFFF" and f[5] == "&H00000000" and f[7] == "-1" and f[15] == "1"
            and 3 <= float(f[16]) <= 4 and float(f[17]) == 0 and "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
            and "ScaledBorderAndShadow: yes" in ass and Path(meme_short.FONT_FILE).name.lower() == "arialbd.ttf"), style


@check
def the_caption_sits_at_14_to_22_percent_or_inside_the_top_bar():
    """Rule 3: full-bleed, the caption's middle is at y 14-22 % of 1920; letterboxed, the whole caption is inside the top
    black bar, three lines too"""
    full = meme_short.caption_box(spec())
    lb = meme_short.caption_box(spec(layout="letterbox",
                                     captions=["Jason tried to park on a hill and this happens...", "Bro is a missile",
                                               "I'm sorry Lucia"]))
    g = meme_short.geometry(spec(layout="letterbox"), 1920, 1080)
    mid = (full[0] + full[1]) / 2
    return 0.14 * 1920 <= mid <= 0.22 * 1920 and 0 < lb[0] and lb[1] < g["picture_y"], (full, lb, g)


# --- Rule 4: layout ---

@check
def the_layout_is_full_bleed_or_letterbox_and_never_blurred():
    """Rule 4: "full" crops 9:16 centred on the subject (kept inside the picture), "letterbox" centres the 16:9 picture
    with black bars; "blur" and anything else are refused"""
    full = meme_short.geometry(spec(focus=[0.7, 0.5]), 1920, 1080)
    edge = meme_short.geometry(spec(focus=[0.99, 0.5]), 1920, 1080)
    lb = meme_short.geometry(spec(layout="letterbox"), 1920, 1080)
    blur = rule(4, spec(layout="blur"))
    no_focus = rule(4, spec(focus=None))
    return (full["crop"] == (608, 1080, 1040, 0) and edge["crop"][2] == 1920 - 608
            and lb["picture"] == (1080, 608) and lb["picture_y"] == 656 and blur and no_focus
            and not rule(4, spec(layout="letterbox"))), (full, edge, lb, blur)


# --- Rule 5: audio ---

@check
def no_ai_voice_and_the_game_sound_at_minus_14():
    """Rule 5: a spec with a voice, a script or narration is refused; the game's sound is brought to -14 LUFS and goes
    through the repo's true-peak limit; the music bed is off unless named"""
    voice = rule(5, spec(voice="am_puck"))
    lines = rule(5, spec(lines=[{"say": "Watch this"}]))
    return (voice and lines and meme_short.TARGET_LUFS == -14.0 and meme_short.TRUE_PEAK_MAX == safety.TRUE_PEAK_MAX
            and meme_short.music_of(spec()) is None and not rule(5, spec())), (voice, lines)


@check
def the_music_bed_ducks_20_db_under_dialogue():
    """Rule 5: an optional "wrong mood" bed from the music folder sits 20 dB under the game while a line is heard and
    rises between lines; a file from anywhere else is refused"""
    heard = [{"from_s": 2.0, "to_s": 4.0, "text": "a"}]
    inside, outside = meme_short.music_gain_db(3.0, heard), meme_short.music_gain_db(7.0, heard)
    ok = rule(5, spec(music="Future - Anno Domini Beats.mp3"))
    away = rule(5, spec(music="C:/Users/someone/song.mp3"))
    return (inside == meme_short.MUSIC_DUCK_DB == -20.0 and outside == meme_short.MUSIC_BED_DB and outside > inside
            and not ok and away), (inside, outside, ok, away)


# --- Rule 6: edits ---

@check
def at_most_one_edit_a_punch_in_or_a_replay():
    """Rule 6: no edit by default; a punch-in of 1.20-1.35x for 1-2 s at the punchline, or one replay that jumps back
    0.5-1 s and plays 2-3.5 s at 0.5x; anything else (two edits, whooshes, booms, arrows, freezes, inserts) is refused"""
    good = [None, {"type": "punch", "at_s": 104.0, "for_s": 1.5, "zoom": 1.25},
            {"type": "replay", "back_s": 0.8, "for_s": 2.8}]
    bad = [{"type": "punch", "at_s": 104.0, "for_s": 1.5, "zoom": 1.1},
           {"type": "punch", "at_s": 104.0, "for_s": 1.5, "zoom": 1.4},
           {"type": "punch", "at_s": 104.0, "for_s": 0.5, "zoom": 1.25},
           {"type": "punch", "at_s": 104.0, "for_s": 2.5, "zoom": 1.25},
           {"type": "punch", "at_s": 102.0, "for_s": 1.5, "zoom": 1.25},
           {"type": "replay", "back_s": 0.3, "for_s": 2.8},
           {"type": "replay", "back_s": 0.8, "for_s": 4.0},
           {"type": "replay", "back_s": 0.8, "for_s": 2.8, "speed": 0.25},
           [{"type": "punch", "at_s": 104.0, "for_s": 1.5, "zoom": 1.25}, {"type": "replay", "back_s": 0.8, "for_s": 2.8}],
           {"type": "freeze"}]
    g = {str(e): rule(6, spec(edit=e, to_s=105.0, reaction_end_s=104.0) if e and isinstance(e, dict)
                         and e.get("type") == "replay" else spec(edit=e)) for e in good}
    b = {str(e): rule(6, spec(edit=e)) for e in bad}
    extras = [rule(6, spec(**{k: True})) for k in ("whoosh", "boom", "arrows", "freeze_frame", "meme_insert", "sfx")]
    return not any(g.values()) and all(b.values()) and all(extras), (g, b, extras)


# --- Rule 7: ending ---

@check
def the_short_hard_cuts_0_5_to_1_5_s_after_the_punchline():
    """Rule 7: the last frame is 0.5-1.5 s after the punchline or the last reaction line (0.3 s and 2.0 s are refused);
    with a replay it is measured from the replay's own punchline; an end card or a subscribe pill is refused"""
    ok = not rule(7, spec())
    soon = rule(7, spec(to_s=108.3))
    late = rule(7, spec(to_s=110.0))
    replay_ok = not rule(7, spec(to_s=104.5, reaction_end_s=None, edit={"type": "replay", "back_s": 0.8, "for_s": 2.8}))
    replay_long = rule(7, spec(to_s=104.5, reaction_end_s=None, edit={"type": "replay", "back_s": 0.5, "for_s": 3.5}))
    talk_ok = not rule(7, talk())
    card = rule(7, spec(end_card="Subscribe"))
    pill = rule(7, spec(end_pill="@NeonGetawayYT"))
    return (ok and soon and late and replay_ok and replay_long and talk_ok and card and pill,
            (soon, late, replay_ok, replay_long, card, pill))


# --- Rule 8: title ---

@check
def the_title_is_short_with_no_hashtags():
    """Rule 8: a short title (60 characters at most, 2 words at least), no hashtag, no @handle, swears censored, no emoji"""
    t = meme_short.title_problems
    good = ["Jason is way too strong", "I'm sorry Lucia"]
    bad = ["Jason is way too strong #gta6", "#shorts", "Jason " * 12, "Wow", "Ask @wisebenda", "Holy shit Jason"]
    return all(not t(x) for x in good) and all(t(x) for x in bad), {x: t(x) for x in good + bad}


@check
def every_rule_is_wired_into_problems():
    """problems() runs every rule: one spec that breaks all eight gets a problem for each, and the good specs get none"""
    bad = spec(kind="gag", to_s=125.0, motion_s=102.0, punchline_s=101.0, reaction_end_s=None, layout="blur",
               captions=["Watch the car"], star=0, voice="am_puck", edit={"type": "freeze"}, end_card="x",
               title="#gta6 #shorts")
    got = meme_short.problems(bad)
    return (all(any(p.startswith(f"rule {n}:") for p in got) for n in range(1, 9)) and not meme_short.problems(spec())
            and not meme_short.problems(talk())), got


# --- Finding moments (read-only) ---

@check
def the_finder_skips_used_footage_and_never_use_parts():
    """find_candidates() reads an index and the used footage only: a funny line inside a used span (any status), a
    rejected span, a never_use segment or a never-shown moment is left out; the rest is ranked by kind"""
    idx = {"every": 6.0, "segments": [
        {"start_s": 0.0, "end_s": 60.0, "never_use": False}, {"start_s": 60.0, "end_s": 90.0, "never_use": True},
        {"start_s": 90.0, "end_s": 200.0, "never_use": False}],
        "sheets": [{"t": 12.0, "notable": "Two men fight in an alley; one knocks down the other."},
                   {"t": 30.0, "notable": "Player drives on the highway."},
                   {"t": 66.0, "notable": "Man yells at the player and runs away."},
                   {"t": 102.0, "notable": "Player falls off a ladder and tumbles."},
                   {"t": 138.0, "notable": "Dog chases a pedestrian on the beach."},
                   {"t": 170.0, "notable": "Player argues with a clerk."}]}
    used = {"shorts": [{"name": "x", "status": "rendered", "spans": [["rec", 100.0, 110.0]]}],
            "rejected": [{"id": "y", "spans": [["rec", 165.0, 180.0]]}]}
    never = [{"video": "rec", "start_s": 135.0, "end_s": 150.0, "why": ["a body"]}]
    got = meme_short.find_candidates({"rec": idx}, used, never)
    at = sorted(round(c["at_s"]) for c in got)
    return at == [12] and got[0]["kind"] == "fight", got


# --- Real renders (tiny, made-up footage) ---

def made_up(folder, seconds=12.0, box=False):
    """A 640x360 30 fps video with a 440 Hz tone. box: a white 80 px square in the middle on grey (for the punch-in);
    else its brightness is the time (25 levels a second), so a frame says which second of the source it shows."""
    out = Path(folder) / ("box.mp4" if box else "ramp.mp4")
    picture = ("color=c=0x404040:s=640x360:r=30,drawbox=x=280:y=140:w=80:h=80:color=white:t=fill" if box else
               "color=c=black:s=640x360:r=30,geq=lum='clip(16+T*15\\,0\\,235)':cb=128:cr=128")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{picture},format=yuv420p", "-f", "lavfi",
                    "-i", "sine=f=440:sample_rate=48000", "-t", f"{seconds}", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "10", "-c:a", "aac", "-shortest", str(out)], check=True, capture_output=True)
    return out


def gray(video, t, w=1080, h=1920):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", "-f",
                          "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    return raw[:w * h]


def rows_with(raw, test, w=1080, x0=0, x1=1080, y0=0, y1=1920, need=6):
    return [y for y in range(y0, y1) if sum(1 for x in range(x0, x1, 2) if test(raw[y * w + x])) >= need]


def render(folder, s, **kw):
    return meme_short.render(s, Path(folder) / "out", source=kw.pop("source"), checks="none", log=lambda *a: None, **kw)


_shared = {}


def full_render():
    """One full-bleed render with a swap, shared by the checks that look at it."""
    if "full" not in _shared:
        tmp = tempfile.mkdtemp(prefix="memetest_")
        src = made_up(tmp)
        s = spec(recording=str(src), from_s=1.0, to_s=10.0, motion_s=1.2, punchline_s=5.0, reaction_end_s=9.0,
                 swap={"text": "THAT strong", "at_s": 5.5})
        _shared["full"] = (render(tmp, s, source=src), s)
    return _shared["full"]


@check
def a_full_bleed_render_has_its_caption_on_the_first_and_last_frames():
    """A real render: 9.0 s long; white caption pixels on the first frame, the last frame and both sides of the swap, all
    with their middle at y 14-22 % of 1920; the caption changes at the swap and not before"""
    log, s = full_render()
    v = Path(log["short"])
    d = log["measured"]["duration_s"]
    boxes = {}
    for name, t in (("first", 0.0), ("before_swap", 4.4), ("after_swap", 4.6), ("last", d - 1 / 30)):
        ys = rows_with(gray(v, t), lambda p: p >= 235, y0=0, y1=900)
        boxes[name] = (min(ys), max(ys)) if ys else None
    mids = [(a + b) / 2 for a, b in filter(None, boxes.values())]
    return (abs(d - 9.0) <= 1 / 30 + 1e-6 and all(boxes.values()) and all(0.14 * 1920 <= m <= 0.22 * 1920 for m in mids)
            and boxes["first"] == boxes["before_swap"] and boxes["after_swap"] == boxes["last"]
            and boxes["before_swap"] != boxes["after_swap"], (d, boxes))


@check
def a_render_is_at_minus_14_lufs_under_the_true_peak_limit():
    """A real render's sound is -14 LUFS (within 1 LU) with its true peak under -1 dBTP, measured on short.mp4"""
    log, _ = full_render()
    m = log["measured"]
    return abs(m["lufs"] - (-14.0)) <= 1.0 and m["true_peak_db"] < safety.TRUE_PEAK_MAX, m


@check
def a_letterbox_render_keeps_black_bars_and_the_caption_in_the_top_bar():
    """A real letterbox render: the picture fills rows 656-1263; above and below it is black but for the caption, which is
    wholly inside the top bar on the first and last frames"""
    tmp = tempfile.mkdtemp(prefix="memetest_")
    src = made_up(tmp, box=True)
    s = spec(recording=str(src), from_s=1.0, to_s=10.0, motion_s=1.2, punchline_s=5.0, reaction_end_s=9.0,
             layout="letterbox")
    log = render(tmp, s, source=src)
    v, d = Path(log["short"]), log["measured"]["duration_s"]
    out, good = {}, True
    for name, t in (("first", 0.0), ("last", d - 1 / 30)):
        raw = gray(v, t)
        cap = rows_with(raw, lambda p: p >= 235, y0=0, y1=656)               # the caption, in the top bar
        picture = rows_with(raw, lambda p: 40 <= p <= 90, y0=0, y1=1920, need=200)
        below = max(raw[y * 1080 + x] for y in range(1270, 1920, 7) for x in range(0, 1080, 7))
        above = max((raw[y * 1080 + x] for y in list(range(0, max(0, min(cap or [0]) - 12), 5))
                     + list(range(max(cap or [0]) + 12, 650, 5)) for x in range(0, 1080, 7)), default=0)
        out[name] = {"caption": (min(cap), max(cap)) if cap else None, "picture": (min(picture), max(picture)),
                     "top_bar_max": above, "bottom_bar_max": below}
        good &= bool(cap) and 0 < min(cap) and max(cap) < 656 and abs(min(picture) - 656) <= 2
        good &= abs(max(picture) - 1263) <= 2 and above <= 24 and below <= 24
    return good, out


@check
def a_punch_in_zooms_toward_the_subject_by_its_factor():
    """A real punch-in of 1.25x at the punchline: the white square is 1.25 times as wide (within 3 %) during it as before
    it, and the render keeps its length"""
    tmp = tempfile.mkdtemp(prefix="memetest_")
    src = made_up(tmp, box=True)
    s = spec(recording=str(src), from_s=1.0, to_s=10.0, motion_s=1.2, punchline_s=5.0, reaction_end_s=9.0,
             edit={"type": "punch", "at_s": 5.0, "for_s": 1.5, "zoom": 1.25})
    log = render(tmp, s, source=src)
    v = Path(log["short"])

    def width(t):
        raw = gray(v, t)
        y = 960                               # far under the caption, through the square's middle
        xs = [x for x in range(1080) if raw[y * 1080 + x] >= 235]
        return max(xs) - min(xs) + 1 if xs else 0
    before, during, after = width(2.0), width(4.6), width(6.0)
    ratio = during / before if before else 0
    return (abs(ratio - 1.25) <= 0.03 * 1.25 and abs(after - before) <= 4
            and abs(log["measured"]["duration_s"] - 9.0) <= 1 / 30 + 1e-6), (before, during, after, ratio)


@check
def a_replay_jumps_back_and_plays_at_half_speed():
    """A real replay: after the main part it shows the source from 0.8 s before the punchline at 0.5x for 2.8 s (the
    source's brightness says which second each frame shows), and the Short is that much longer"""
    tmp = tempfile.mkdtemp(prefix="memetest_")
    src = made_up(tmp)
    s = spec(recording=str(src), from_s=1.0, to_s=5.5, motion_s=1.2, punchline_s=5.0, reaction_end_s=None,
             edit={"type": "replay", "back_s": 0.8, "for_s": 2.8})
    log = render(tmp, s, source=src)
    v = Path(log["short"])

    def source_second(t):
        raw = gray(v, t)
        vals = [raw[y * 1080 + x] for y in range(1300, 1700, 9) for x in range(100, 980, 9)]
        return (sum(vals) / len(vals) - 16) / 15
    main, r0, r1 = source_second(2.0), source_second(4.6), source_second(6.6)
    # main part: 2.0 s in is source 3.0 s; the replay starts at 4.5 s on source 4.2 s and runs at half speed
    return (abs(main - 3.0) <= 0.15 and abs(r0 - (4.2 + 0.05)) <= 0.15 and abs(r1 - (4.2 + 1.05)) <= 0.15
            and abs(log["measured"]["duration_s"] - 7.3) <= 1 / 30 + 1e-6), (main, r0, r1, log["measured"])


@check
def meme_mode_writes_its_own_log_and_nothing_launch_reads():
    """The render writes short.mp4, captions.txt (3 options, the starred one marked), title.txt and meme.json, and no
    render.json (launch.py reads every render.json under the shorts folder)"""
    log, s = full_render()
    folder = Path(log["short"]).parent
    caps = (folder / "captions.txt").read_text(encoding="utf-8").splitlines()
    starred = [c for c in caps if c.startswith("*")]
    data = json.loads((folder / "meme.json").read_text(encoding="utf-8"))
    return ((folder / "short.mp4").is_file() and (folder / "title.txt").read_text(encoding="utf-8").strip() == s["title"]
            and len([c for c in caps if c.strip()]) == 3 and len(starred) == 1 and s["captions"][0] in starred[0]
            and not (folder / "render.json").exists() and data["spec"]["title"] == s["title"]), caps


def main():
    ok = True
    for fn in testkit.selected(CHECKS):
        try:
            got = fn()
            good, err = (bool(got[0]), f"  (got {got[1]})") if isinstance(got, tuple) else (bool(got), "")
            err = err if not good else ""
        except Exception as e:
            good, err = False, f"  ({type(e).__name__}: {e})"
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'} {fn.__name__}: {' '.join((fn.__doc__ or '').split())}{err[:600]}",
              flush=True)
    print(f"RESULT: {'PASS' if ok else 'FAIL'} ({len(testkit.selected(CHECKS))} checks)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
