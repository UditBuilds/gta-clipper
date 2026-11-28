"""Checks for meme mode (meme_short.py, PR 39): rules 1-8 of the owner's brief of Nov 27, each with specs that must pass
and specs that must fail, and tiny real renders whose first and last frames are measured on the pixels (the caption's
place and its swap, the letterbox bars, the punch-in's zoom, the replay's slow motion, the loudness and the true peak).
No Ollama, no Whisper, no footage: the renders use made-up 16:9 videos.

  .venv\\Scripts\\python.exe memetest.py
"""
import json
import re
import struct
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import index
import meme_marked
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


def series(pic_first, snd_first, pic_rest=8.0, snd_rest=-25.0, seconds=10.0, snd_at=0.0):
    """Made-up measurements every 0.1 s: picture change pic_first in the first second and pic_rest after it; sound
    snd_first (dB) from snd_at to 1.0 s and snd_rest after it, -60 dB before snd_at."""
    n = round(seconds / meme_short.STEP_S)
    pic = [pic_first if i * meme_short.STEP_S < 1.0 else pic_rest for i in range(n)]
    snd = [-60.0 if i * meme_short.STEP_S < snd_at - 1e-9 else snd_first if i * meme_short.STEP_S < 1.0 else snd_rest
           for i in range(n)]
    return pic, snd


@check
def the_measured_first_second_is_compared_with_the_rest_of_the_cut():
    """Rule 2 measured (PR 39's blocker: the old flag compared with the bank's z-scores, whose median is 0 by
    construction, so it never fired): the first second's picture change against the rest's busy level (its 75th
    percentile), and the first 0.5 s of sound against the rest's loud level. A still, quiet start is refused; motion
    in the first second or a sound by 0.5 s passes; a sound that starts at 0.7 s does not save a still picture"""
    v = meme_short.start_verdict
    dead = v(*series(0.5, -40.0))[1]
    moving = v(*series(6.0, -40.0))[1]
    line = v(*series(0.5, -26.0))[1]
    late_line = v(*series(0.5, -26.0, snd_at=0.7))[1]
    return dead and not moving and not line and late_line, (dead, moving, line, late_line)


def made_up_start(folder, still_s):
    """A 640x360 video, 10 s: still grey and silent for still_s, then a white bar that moves and a 440 Hz tone."""
    out = Path(folder) / f"start_{still_s:g}.mp4"
    picture = (f"color=c=black:s=640x360:r=30,geq=lum='if(between(X\\,max(0\\,T-{still_s})*{BAR_PX_S}\\,"
               f"max(0\\,T-{still_s})*{BAR_PX_S}+7)*gte(T\\,{still_s})\\,235\\,71)':cb=128:cr=128")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{picture},format=yuv420p", "-f", "lavfi",
                    "-i", "sine=f=440:sample_rate=48000", "-af", f"volume='if(lt(t\\,{still_s})\\,0\\,1)':eval=frame",
                    "-t", "10", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10", "-c:a", "aac", "-shortest",
                    str(out)], check=True, capture_output=True)
    return out


@check
def a_dead_start_is_measured_on_the_footage_and_refused():
    """Rule 2 measured on a real file: a cut whose first 2 s are still and silent is refused (and a render that measures
    its start stops before it encodes anything); the same cut starting where the bar moves and the tone sounds passes"""
    tmp = tempfile.mkdtemp(prefix="memetest_")
    src = made_up_start(tmp, 2.0)
    dead = meme_short.measure_start(src, 0.0, 9.0)
    live = meme_short.measure_start(src, 2.0, 7.5)
    s = spec(recording=str(src), from_s=0.0, to_s=9.0, motion_s=0.5, punchline_s=4.0, reaction_end_s=8.0)
    try:
        render(tmp, s, source=src, measure_start=True)
        refused = []
    except meme_short.MemeError as e:
        refused = [str(e)]
    return (dead[1] and not live[1] and refused and "rule 2" in refused[0]
            and not (Path(tmp) / "out" / "short.mp4").exists()), (dead, live, refused)


@check
@testkit.full
@testkit.needs("gta")
def the_measured_first_second_fires_on_a_real_dead_start_and_passes_samples_1_and_2():
    """Rule 2 measured on the owner's footage: samples 1 (the GPS crash, moving from 0.0 s) and 2 (the bear protest, a
    line at 0.4 s) pass; a cut of the same bear recording at 4:30-4:40, where Jason stands still by the truck for 2.5 s
    (clip.py's loudness and picture change both under the recording's median there), is refused"""
    out = {}
    for name in ("1-the-gps-said-turn-right", "2-free-the-bear"):
        s = json.loads((Path(__file__).parent / "examples" / "meme" / f"{name}.json").read_text(encoding="utf-8"))
        src = Path(r"C:\AI\gta\input") / f"{s['recording']}.webm"
        out[name] = meme_short.measure_start(src, s["from_s"], s["to_s"] - s["from_s"])
    dead_src = Path(r"C:\AI\gta\input\Grand Theft Auto VI_20261121223830.webm")
    out["dead 4:30"] = meme_short.measure_start(dead_src, 270.0, 10.0)
    return (not out["1-the-gps-said-turn-right"][1] and not out["2-free-the-bear"][1] and out["dead 4:30"][1]), out


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
    """Rule 3: 2-6 words; up to 10 only for "<Subject> tried <X> and this happens..."; 1, 7 and 11 words are refused;
    "a", "an" and "the" don't count, so the owner's four real examples pass"""
    c = meme_short.caption_problems
    good = ["I'm sorry Lucia", "Bro is a missile now", "Jason tried parking and this happens...",
            "Jason tried to park on a hill and this happens...", "Jason Became a Gorilla for a Sec",
            "Didn't know Jason was this strong", "The slowest robbery in history"]
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

BAR_PX_S = 50                   # the "bar" video's white bar moves this many source pixels a second


def made_up(folder, seconds=12.0, box=False, bar=False):
    """A 640x360 30 fps video with a 440 Hz tone. box: a white 80 px square in the middle on grey (for the punch-in);
    bar: a white 8 px bar on grey whose left edge is at BAR_PX_S x the time, so a frame says which second of the source it
    shows; else a dark picture that gets lighter with time and is never white (under the caption)."""
    out = Path(folder) / ("box.mp4" if box else "bar.mp4" if bar else "ramp.mp4")
    picture = ("color=c=0x404040:s=640x360:r=30,drawbox=x=280:y=140:w=80:h=80:color=white:t=fill" if box else
               f"color=c=black:s=640x360:r=30,geq=lum='if(between(X\\,T*{BAR_PX_S}\\,T*{BAR_PX_S}+7)\\,235\\,71)'"
               ":cb=128:cr=128" if bar else
               "color=c=black:s=640x360:r=30,geq=lum='clip(16+T*15\\,0\\,235)':cb=128:cr=128")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{picture},format=yuv420p", "-f", "lavfi",
                    "-i", "sine=f=440:sample_rate=48000", "-t", f"{seconds}", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "10", "-c:a", "aac", "-shortest", str(out)], check=True, capture_output=True)
    return out


def gray(video, t, w=1080, h=1920):
    """The gray pixels of the frame on screen at t (picked by its number: a seek by time can land a frame off)."""
    n = int(round(t * 30))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"select=eq(n\\,{n})", "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
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
    """A real replay: after the main part it shows the source from 0.8 s before the punchline at 0.5x for 2.8 s (a
    moving bar says which second of the source each frame shows, to about a frame), and the Short is that much longer"""
    tmp = tempfile.mkdtemp(prefix="memetest_")
    src = made_up(tmp, bar=True)
    s = spec(recording=str(src), from_s=1.0, to_s=5.5, motion_s=1.2, punchline_s=5.0, reaction_end_s=None,
             layout="letterbox", edit={"type": "replay", "back_s": 0.8, "for_s": 2.8})
    log = render(tmp, s, source=src)
    v = Path(log["short"])

    def source_second(t):
        raw = gray(v, t)
        xs = [x for x in range(1080) if raw[960 * 1080 + x] >= 235]    # the bar, through the picture's middle
        return min(xs) / (1080 / 640) / BAR_PX_S if xs else None
    main, r0, r1 = source_second(2.0), source_second(4.6), source_second(6.6)
    # 2.0 s in is source 3.0 s; the replay starts at 4.5 s on source 4.2 s and runs at half speed: 3 frames in it is
    # source frame 1 (4.233 s), 63 frames in source frame 31 (5.233 s)
    near = (lambda got, want: got is not None and abs(got - want) <= 1.5 / 30)
    return (near(main, 3.0) and near(r0, 4.2 + 1 / 30) and near(r1, 4.2 + 31 / 30)
            and abs(log["measured"]["duration_s"] - 7.3) <= 1 / 30 + 1e-6), (main, r0, r1, log["measured"])


@check
def a_length_that_is_not_whole_frames_keeps_every_frame():
    """A cut whose length rounds UP to whole frames (9.16 s = 274.8 frames -> 275) keeps all 275: the sound is cut to the
    same whole frames as the picture, so the final mux's -shortest never ends on the sound first and drops the last
    frames the B-frames hold back (marked mode's first unindexed run lost 4 of 335 and failed rule 1)"""
    tmp = tempfile.mkdtemp(prefix="memetest_")
    src = made_up(tmp)
    s = spec(recording=str(src), from_s=1.0, to_s=10.16, motion_s=1.2, punchline_s=5.0, reaction_end_s=9.0)
    try:
        log = render(tmp, s, source=src)
    except meme_short.MemeError as e:
        return False, str(e)
    frames = int(meme_short.ex.stream_of(meme_short.ex.probe(log["short"]), "video").get("nb_frames") or 0)
    return frames == 275, (frames, log["measured"])


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


# --- Marked mode (Nov 28): the owner marks the moments, the pipeline only edits ---

def rec_of(name, duration):
    start, end = meme_marked.span_of(name, duration)
    return {"name": name, "path": f"C:/made_up/{name}", "duration": duration, "start": start, "end": end}


@check
def a_recording_ends_at_its_file_name_time():
    """A PS5 file name's time is when the recording ENDED (it was saved), in IST: start = that time less its length.
    Measured on the owner's files (Nov 28): read as the start, three back-to-back pairs would overlap by 450-600 s and
    their frames differ; read as the end, every gap is positive. Here: the three pairs keep a gap of 0 s or more"""
    start, end = meme_marked.span_of("Grand Theft Auto VI_20261119014828.webm", 978.6)
    pairs = [("Grand Theft Auto VI_20261119014828", 978.6, "Grand Theft Auto VI_20261119015444", 304.6),
             ("Grand Theft Auto VI_20261119022442", 1031.5, "Grand Theft Auto VI_20261119023422", 537.8),
             ("Grand Theft Auto VI_20261119035639", 1140.7, "Grand Theft Auto VI_20261119040546", 450.6)]
    gaps = [(meme_marked.span_of(b, db)[0] - meme_marked.span_of(a, da)[1]).total_seconds() for a, da, b, db in pairs]
    return (end == datetime(2026, 11, 19, 1, 48, 28) and start == end - timedelta(seconds=978.6)
            and meme_marked.stamp_of("test1.webm") is None and all(g >= 0 for g in gaps)), (start, end, gaps)


@check
def marks_txt_lines_are_read_with_their_date_and_precision():
    """marks.txt: one moment a line, "<clock time> <a few words>"; HH:MM or HH:MM:SS, "pm"/"am" allowed; a line with
    only a date (2026-11-28) dates the lines under it; # and blank lines are skipped; a line it can't read is kept to
    be listed, never dropped; the same line twice is two moments (the second keyed "#2")"""
    text = ("# my marks\n2026-11-28\n11:47 bear attacks protesters\n11:47:30 car flips\n\n7:05 pm truck jump\n"
            "hello there\n11:47 bear attacks protesters\n")
    marks, bad = meme_marked.parse_marks(text)
    m0, m1, m2, m3 = (marks + [None] * 4)[:4]
    return (len(marks) == 4 and m0["date"] == date(2026, 11, 28) and (m0["h"], m0["m"], m0["s"]) == (11, 47, 0)
            and m0["precision"] == "minute" and m0["words"] == "bear attacks protesters"
            and m1["precision"] == "second" and m1["s"] == 30 and (m2["h"], m2["m"], m2["twelve"]) == (19, 5, False)
            and m0["twelve"] and m3["key"] == m0["key"] + " #2" and m0["key"] != m3["key"]
            and [t for _, t in bad] == ["hello there"]), (marks, bad)


@check
def an_undated_mark_is_the_latest_such_time_before_marks_txt_was_saved():
    """A mark with no date line above it is the latest moment with that clock time at or before marks.txt was last
    saved (23:58 saved at 00:10 is the day before); a time before 12:00 with no am/pm can also be the afternoon, so
    both are tried (24-hour reading first); a dated mark is that day's"""
    c = meme_marked.candidates
    marks, _ = meme_marked.parse_marks("23:58 crash\n11:47 bear\n2026-11-21\n16:35:20 gps\n4:35 x\n")
    saved = datetime(2026, 11, 29, 0, 10)
    got = [c(m, saved) for m in marks]
    want = [[datetime(2026, 11, 28, 23, 58)], [datetime(2026, 11, 28, 11, 47), datetime(2026, 11, 28, 23, 47)],
            [datetime(2026, 11, 21, 16, 35, 20)], [datetime(2026, 11, 21, 4, 35), datetime(2026, 11, 21, 16, 35)]]
    return got == want, got


@check
def a_mark_is_placed_in_the_recording_that_covers_it_with_its_window():
    """The recording whose span covers the mark, and the window 20 s before to 5 s after it (a mark to the second);
    a mark to the minute covers its minute (20 s before :00 to 5 s after :59); the window is cut to the recording; a
    time no recording covers gives the reason (and the nearest recording), never nothing; the afternoon reading is used
    only when the morning one is not covered, and the pick says so"""
    recs = [rec_of("Grand Theft Auto VI_20261121164843.webm", 1753.8),
            rec_of("Grand Theft Auto VI_20261121223830.webm", 371.8)]
    sec = meme_marked.place([datetime(2026, 11, 21, 16, 35, 20)], recs, "second")
    minute = meme_marked.place([datetime(2026, 11, 21, 16, 35)], recs, "minute")
    edge = meme_marked.place([datetime(2026, 11, 21, 22, 38, 28)], recs, "second")
    none = meme_marked.place([datetime(2026, 11, 21, 20, 0)], recs, "minute")
    pm = meme_marked.place([datetime(2026, 11, 21, 4, 35), datetime(2026, 11, 21, 16, 35)], recs, "minute")
    near = (lambda got, want: got is not None and all(abs(x - y) < 0.01 for x, y in zip(got, want)))
    return (sec.get("recording", {}).get("name", "").endswith("164843.webm") and near(sec.get("window"), (930.8, 955.8))
            and near(minute.get("window"), (910.8, 994.8)) and near(edge.get("window"), (349.8, 371.8))
            and "no recording covers" in none.get("reason", "") and "223830" in none.get("reason", "")
            and near(pm.get("window"), (910.8, 994.8)) and "afternoon" in pm.get("note", "")), (sec, minute, edge, none, pm)


@check
def a_saved_clip_is_searched_in_its_last_25_s():
    """A PS5 "Save recent gameplay" clip ends just after the moment: the punch is looked for in its last 25 s (all of
    it when shorter)"""
    return meme_marked.clip_window(60.0) == (35.0, 60.0) and meme_marked.clip_window(12.0) == (0.0, 12.0), (
        meme_marked.clip_window(60.0), meme_marked.clip_window(12.0))


@check
def the_done_record_keeps_rendered_moments_and_retries_the_rest():
    """The record of what is done: a rendered moment is never made again; a failed or waiting one is tried again on the
    next run; a mark's clock times are worked out once, when it is first seen, and kept (saving marks.txt again
    tomorrow doesn't move it); a record that can't be read stops the run (never read as empty)"""
    tmp = Path(tempfile.mkdtemp(prefix="memetest_"))
    path = tmp / "marked_record.json"
    rec = meme_marked.read_record(path)
    meme_marked.set_entry(rec, "k1", status="rendered", folder="x")
    meme_marked.set_entry(rec, "k2", status="failed", reason="rule 2")
    meme_marked.set_entry(rec, "k3", status="waiting", reason="no recording covers it")
    todo = meme_marked.todo(["k1", "k2", "k3", "k4"], rec)
    meme_marked.write_record(path, rec)
    back = meme_marked.read_record(path)
    mark = meme_marked.parse_marks("23:58 crash\n")[0][0]
    first = meme_marked.times_for(mark, datetime(2026, 11, 29, 0, 10), back)
    later = meme_marked.times_for(mark, datetime(2026, 11, 30, 9, 0), back)
    (tmp / "bad.json").write_text("{not json", encoding="utf-8")
    try:
        meme_marked.read_record(tmp / "bad.json")
        damaged = False
    except index.StateError:
        damaged = True
    return (todo == ["k2", "k3", "k4"] and meme_marked.entry(back, "k1")["status"] == "rendered"
            and first == later == [datetime(2026, 11, 28, 23, 58)] and damaged), (todo, first, later)


def spike_series(seconds=30.0, at=12.0, base_pic=2.0, base_snd=-30.0, before=None):
    """Made-up measurements from 0 s: a spike of picture change and sound for 0.5 s at `at`; `before`: (pic, snd) for
    everything before the spike instead of the base (a still, quiet lead-in)."""
    n = round(seconds / meme_short.STEP_S)
    pic, snd = [], []
    for i in range(n):
        t = i * meme_short.STEP_S
        if at - 1e-9 <= t < at + 0.5:
            pic.append(20.0), snd.append(-12.0)
        elif before is not None and t < at:
            pic.append(before[0]), snd.append(before[1])
        else:
            pic.append(base_pic), snd.append(base_snd)
    return pic, snd


@check
def the_punch_is_the_biggest_sound_and_motion_spike_in_the_window():
    """The punch is where sound and picture jump most inside the window (a bigger spike outside it is not taken),
    at the jump's start"""
    pic, snd = spike_series(seconds=40.0, at=12.0)
    for i in range(round(30.0 / meme_short.STEP_S), round(31.0 / meme_short.STEP_S)):
        pic[i], snd[i] = 40.0, -5.0                  # a bigger spike at 30 s, outside the window
    p = meme_marked.find_punch(pic, snd, 100.0, (105.0, 120.0))
    return abs(p["at_s"] - 112.0) <= 0.15, p


def finished(plan, **kw):
    s = {"recording": "made_up", "kind": "gag", "layout": "letterbox", "focus": [0.5, 0.5],
         "captions": ["Bro trusted the GPS", "Nobody saw that coming", "Just another day for Jason"], "star": 0,
         "title": "Bro trusted the GPS"}
    s.update({k: plan[k] for k in ("from_s", "to_s", "punchline_s", "reaction_end_s", "motion_s", "heard")})
    s.update(kw)
    return s


@check
def the_cut_keeps_the_meme_rules_and_ends_after_the_reaction():
    """The cut around a punch: a 7-12 s gag, the punch at 0:02-0:06, a hard cut 0.5-1.5 s after the last line heard
    around it, a first second that passes rule 2 measured; a reaction line too long to fit is left out; a cut that can
    only start still and quiet is not made, and says why"""
    pic, snd = spike_series(seconds=30.0, at=12.0)
    line = [{"from_s": 112.5, "to_s": 115.0, "text": "What the hell, man"}]
    plan = meme_marked.plan_cut(112.0, line, pic, snd, 100.0, 130.0)
    ok = (not plan.get("reason") and not meme_short.problems(finished(plan))
          and 2.0 <= plan["punchline_s"] - plan["from_s"] <= 6.0 and 0.5 <= plan["to_s"] - 115.0 <= 1.5)
    long_line = meme_marked.plan_cut(112.0, [{"from_s": 112.5, "to_s": 124.0, "text": "a long rant"}], pic, snd, 100.0,
                                     130.0)
    long_ok = (not long_line.get("reason") and not meme_short.problems(finished(long_line))
               and long_line["to_s"] <= 112.0 + 1.5 + 1e-6)
    dead_pic, dead_snd = spike_series(seconds=30.0, at=12.0, base_pic=8.0, base_snd=-20.0, before=(0.2, -50.0))
    dead = meme_marked.plan_cut(112.0, [], dead_pic, dead_snd, 100.0, 130.0)
    return ok and long_ok and "rule 2" in dead.get("reason", ""), (plan, long_line, dead)


@check
def the_reaction_runs_on_through_lines_that_follow_and_the_cut_misses_the_next_line():
    """Lines that follow each other within 1.5 s are one reaction (the GPS crash's "My f-ing ride, bruh!" came 0.9 s after
    "The f-, man?" and was cut off 0.1 s in): the cut comes after the last of them that fits; the hard cut never lands
    inside the next line when 0.5-1.5 s leaves room before it"""
    pic, snd = spike_series(seconds=30.0, at=12.0)
    chain = [{"from_s": 112.4, "to_s": 113.0, "text": "Oh no!"}, {"from_s": 113.5, "to_s": 116.0, "text": "What the hell?"},
             {"from_s": 116.9, "to_s": 119.0, "text": "My ride!"}, {"from_s": 121.5, "to_s": 123.0, "text": "unrelated"}]
    a = meme_marked.plan_cut(112.0, chain, pic, snd, 100.0, 130.0)
    close = [{"from_s": 112.5, "to_s": 115.0, "text": "What the hell?"},
             {"from_s": 115.7, "to_s": 124.5, "text": "a long rant that can't fit"}]
    b = meme_marked.plan_cut(112.0, close, pic, snd, 100.0, 130.0)
    return (a.get("reaction_end_s") == 119.0 and 119.5 <= a.get("to_s", 0) <= 120.5 and not meme_short.problems(finished(a))
            and b.get("reaction_end_s") == 115.0 and 115.5 - 1e-6 <= b.get("to_s", 0) <= 115.65
            and not meme_short.problems(finished(b))), (a, b)


@check
def captions_come_from_what_is_seen_and_never_from_swears():
    """The model's captions are kept only when they keep rule 3 and carry no swear word (written censored or not);
    the first kept one is starred; when fewer than 3 are left the rest are filled in and the pick says so; a title with
    a hashtag is replaced by the starred caption"""
    answer = {"seen": "A truck hits Jason on the road", "captions": [
        "Bro got fucking destroyed", "Watch the truck", "Jason vs the truck", "Delivery came early", "Jason vs the truck",
        "This is sht"], "title": "Jason got hit by a truck #gta6"}
    caps, star, title, notes = meme_marked.pick_captions(answer)
    clean = all(not meme_short.caption_problems(c) and meme_short.censor(c) == c and not re.search(r"(?i)\bsht\b", c)
                for c in caps)
    return (len(caps) == 3 and caps[:2] == ["Jason vs the truck", "Delivery came early"] and star == 0
            and title == "Jason vs the truck" and clean and any("filled" in n for n in notes)), (caps, star, title, notes)


class Stand:
    """Stand-ins for marked mode's tools: no ffmpeg, Whisper, Ollama or render. Each made-up video has a spike at
    spike_at[name] seconds."""

    def __init__(self, durations, spike_at):
        self.durations, self.spike_at, self.renders = durations, spike_at, []

    def probe(self, path):
        return self.durations[Path(path).name]

    def measure(self, path, a, seconds):
        pic, snd = spike_series(seconds=self.durations[Path(path).name], at=self.spike_at[Path(path).name])
        i0, i1 = round(a / meme_short.STEP_S), round((a + seconds) / meme_short.STEP_S)
        return pic[i0:i1], snd[i0:i1]

    def hear(self, path, a, b):
        return []

    def caption(self, path, punch, lines, work):
        return {"seen": "A car flips over", "captions": ["Bro forgot how cars work", "Gravity said no",
                                                         "Just another day for Jason"], "title": "Bro forgot how cars work"}

    def index_of(self, path):
        return None

    def render(self, spec, folder, source, idx):
        self.renders.append((Path(source).name, spec["from_s"], spec["to_s"]))
        Path(folder).mkdir(parents=True, exist_ok=True)
        (Path(folder) / "short.mp4").write_bytes(b"made up")
        return {"short": str(Path(folder) / "short.mp4"), "flags": [], "measured": {"duration_s": spec["to_s"] -
                                                                                  spec["from_s"]}}

    def unload(self):
        pass


@check
def a_marked_run_makes_each_moment_once_and_lists_every_one_it_could_not():
    """One run: a saved clip and a mark in a copied recording are cut and rendered; a mark no recording covers, a line
    it can't read and a mark on the same moment as the clip are listed in picks.md with the reason; a second run renders
    nothing again and picks.md still lists everything"""
    tmp = Path(tempfile.mkdtemp(prefix="memetest_"))
    paths = meme_marked.Paths(marks=tmp / "marks.txt", clips=tmp / "marked", record=tmp / "marked" / "record.json",
                              input=tmp / "input", out=tmp / "shorts")
    paths.clips.mkdir()
    paths.input.mkdir()
    clip_name, rec_name = "Grand Theft Auto VI_20261128120000.webm", "Grand Theft Auto VI_20261128115500.webm"
    (paths.clips / clip_name).write_bytes(b"x")                 # 11:59:00-12:00:00, the moment at 50 s (11:59:50)
    (paths.input / rec_name).write_bytes(b"x")                  # 11:45:00-11:55:00, the moment at 310 s (11:50:10)
    paths.marks.write_text("2026-11-28\n11:50:05 car flips\n13:00 nothing here\nnonsense line\n11:59:52 same as clip\n",
                           encoding="utf-8")
    tools = Stand({clip_name: 60.0, rec_name: 600.0}, {clip_name: 50.0, rec_name: 310.0})
    now = datetime(2026, 11, 28, 14, 0)
    first = meme_marked.run(paths, now, tools, log=lambda *a: None)
    renders_first = list(tools.renders)
    second = meme_marked.run(paths, now, tools, log=lambda *a: None)
    page = (paths.out / "marked-2026-11-28" / "picks.md").read_text(encoding="utf-8")
    rec = meme_marked.read_record(paths.record)
    rendered = [e for e in rec["items"] if e["status"] == "rendered"]
    return (len(renders_first) == 2 and tools.renders == renders_first and len(rendered) == 2
            and {r[0] for r in renders_first} == {clip_name, rec_name}
            and abs(renders_first[0][1] - (50.0 - 3.0)) <= 3.1 and "no recording covers" in page
            and "nonsense line" in page and "same moment" in page and page.count("short.mp4") >= 2
            and first["rendered"] == 2 and second["rendered"] == 0), (first, second, renders_first, page[:1500])


class FailOnce(Stand):
    """Stand-ins whose first render writes a short.mp4 and then fails a check (as a real render can, after the mux)."""

    def render(self, spec, folder, source, idx):
        if not self.renders:
            self.renders.append(None)
            Path(folder).mkdir(parents=True, exist_ok=True)
            (Path(folder) / "short.mp4").write_bytes(b"bad")
            raise meme_short.MemeError("rule 1: the file is 9.033 s; the spec says 9.16 s")
        return super().render(spec, folder, source, idx)


@check
def a_failed_moment_is_tried_again_in_its_own_folder_and_its_bad_file_renamed():
    """A render that fails after writing short.mp4 leaves it renamed short.FAILED.mp4 (never uploaded by mistake); the
    next run tries the moment again in the same folder, not a new numbered one"""
    tmp = Path(tempfile.mkdtemp(prefix="memetest_"))
    paths = meme_marked.Paths(marks=tmp / "marks.txt", clips=tmp / "marked", record=tmp / "marked" / "record.json",
                              input=tmp / "input", out=tmp / "shorts")
    paths.clips.mkdir()
    name = "Grand Theft Auto VI_20261128120000.webm"
    (paths.clips / name).write_bytes(b"x")
    tools = FailOnce({name: 60.0}, {name: 50.0})
    now = datetime(2026, 11, 28, 14, 0)
    first = meme_marked.run(paths, now, tools, log=lambda *a: None)
    day = paths.out / "marked-2026-11-28"
    after_first = sorted(p.name for p in day.iterdir() if p.is_dir())
    failed_file = [p.name for p in (day / after_first[0]).iterdir()] if after_first else []
    second = meme_marked.run(paths, now, tools, log=lambda *a: None)
    after_second = sorted(p.name for p in day.iterdir() if p.is_dir())
    return (first["failed"] == 1 and second["rendered"] == 1 and after_first == after_second and len(after_first) == 1
            and "short.FAILED.mp4" in failed_file and "short.mp4" not in failed_file), (first, second, after_first,
                                                                                       after_second, failed_file)


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
