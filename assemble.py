"""Engine v4 (editor-v3): puts a payoff-first Short together once its shots are made (steady.py) and its lines are
placed (cutplan.py). The picture decides the timing; the voice goes into it. Reuses explainer.py's pieces that work:
the Kokoro voice, the yellow word captions, the open card (the hook from frame 0), the subscribe pill, the music bed,
the whooshes, the loudness and the true-peak limit. No loop ending: it replayed the first shot.

Needs numpy and Kokoro: .venv-tts only.
"""
import json
import math
import shutil
from pathlib import Path

import explainer as ex

CRASH_LUFS = -15.0           # an action payoff with no voice over it: the game's own sound comes up to about this
GAME_PAUSE_S = 0.6           # the game's sound rises only in voice pauses this long (a 0.34 s breath let a sideswipe
                             # through and Whisper heard "We're" for "You're", Nov 20)
END_PILL = "@NeonGetaway • Subscribe"


def speak(kokoro, lines, voice, speed):
    """Each line on its own: [{"audio" (24 kHz mono), "rate", "seconds", "words": [...] from the line's start}]."""
    import numpy as np
    out, sentence = [], 0
    for line in lines:
        parts, words, t, rate = [], [], 0.0, 24000
        for k, sent in enumerate(ex.split_sentences(line["say"])):
            audio, rate, tokens, times, _ = ex.speak_sentence(kokoro, sent, voice, speed)
            if k:
                parts.append(np.zeros(int(ex.GAP_S * rate), dtype=np.float32))
                t += ex.GAP_S
            for tok, (w0, w1) in zip(tokens, times):
                words.append({"text": tok.strip(".,;:\"'()[]…—–“”‘’").upper(),
                              "key": ex.word_key(tok), "start": t + w0, "end": t + w1, "sentence": sentence,
                              "pause": tok[-1] in ",;:—", "colour": None, "tag": None})
            parts.append(audio.astype(np.float32))
            t += len(audio) / rate
            sentence += 1
        out.append({"audio": np.concatenate(parts), "rate": rate, "seconds": t, "words": words})
    return out


def voice_track(spoken, placed, total_s):
    """The whole voice (24 kHz mono) with each line at its placed start, and every word at its Short time."""
    import numpy as np
    rate = spoken[0]["rate"] if spoken else 24000
    audio = np.zeros(int(math.ceil(total_s * rate)), dtype=np.float32)
    words = []
    for sp, p in zip(spoken, placed):
        i = round(p["start"] * rate)
        piece = sp["audio"][:max(0, len(audio) - i)]
        audio[i:i + len(piece)] += piece
        words += [{**w, "start": w["start"] + p["start"], "end": w["end"] + p["start"]} for w in sp["words"]]
    return audio, rate, words


def game_track(video, shots, voice, work, quiet=None):
    """The footage's own sound: each shot's stretch of the recording where the shot is, short fades at the cuts,
    set GAME_LUFS under the voice and up GAME_LIFT_DB in pauses of GAME_PAUSE_S or more; quiet: (a, b) of the Short
    with no voice (an action payoff, and its replay), brought up to about CRASH_LUFS. A slowed shot (the replay)
    plays its sound slowed the same way, an octave down at 0.5. Returns (samples, info)."""
    import numpy as np
    n = len(voice)
    track = np.zeros((n, 2), dtype=np.float32)
    for s in shots:
        raw = Path(work) / f"game_{s['n']}.f32"
        speed = s.get("speed", 1.0)
        slow = f",asetrate={ex.RATE * speed:g},aresample={ex.RATE}" if speed != 1.0 else ""
        ex.ffmpeg(["-ss", f"{s['from']:.4f}", "-t", f"{s['to'] - s['from']:.4f}", "-i", video, "-vn", "-af",
                   f"aresample={ex.RATE}{slow}", *ex.F32, raw])
        piece = np.fromfile(raw, np.float32)
        piece = piece[:len(piece) // 2 * 2].reshape(-1, 2)
        i = round(s["start"] * ex.RATE)
        piece = piece[:max(0, min(len(piece), round((s["end"] - s["start"]) * ex.RATE), n - i))]
        if len(piece) < 2:
            continue
        ramp = ex.fade(len(piece), ex.GAME_FADE_S)
        track[i:i + len(piece)] += piece * (ramp * ramp[::-1])[:, None].astype(np.float32)
    raw = Path(work) / "game.f32"
    track.tofile(raw)
    level = ex.measure_loudness(raw, ex.F32)[0]
    if not math.isfinite(level):
        return None, {}
    gap, ex.LIFT_GAP_S = ex.LIFT_GAP_S, GAME_PAUSE_S
    try:
        curve = ex.duck_curve(voice[:, 0]) * (ex.GAME_LIFT_DB / ex.GAP_LIFT_DB)
    finally:
        ex.LIFT_GAP_S = gap
    lift = np.interp(np.arange(n) / ex.RATE, (np.arange(len(curve)) + 0.5) * 0.01, curve)
    out = track * (10 ** ((ex.GAME_LUFS - level + lift) / 20))[:, None].astype(np.float32)
    info = {"recorded_lufs": round(level, 1), "quiet_lift_db": None}
    if quiet and quiet[1] - quiet[0] > 0.4:
        a, b = max(0, round(quiet[0] * ex.RATE)), min(n, round(quiet[1] * ex.RATE))
        seg = Path(work) / "quiet.f32"
        out[a:b].tofile(seg)
        now = ex.measure_loudness(seg, ex.F32)[0]
        extra = float(min(9.0, max(0.0, CRASH_LUFS - now))) if math.isfinite(now) else 0.0
        bump = np.zeros(n)
        bump[a:b] = extra
        ramp = min(round(0.15 * ex.RATE), a, n - b)
        if ramp > 0:
            bump[a - ramp:a] = np.linspace(0, extra, ramp)
            bump[b:b + ramp] = np.linspace(extra, 0, ramp)
        out *= (10 ** (bump / 20))[:, None].astype(np.float32)
        info.update(quiet_was_lufs=round(now, 1), quiet_lift_db=round(extra, 1))
    return out, info


def end_pill():
    pill = {"text": END_PILL, "style": "info", "pos": "bottom", "font": ex.PILL_SIZE, "pad": ex.PILL_PAD,
            "fill": (255, 255, 255), "ink_colour": ex.INK, "border": 0, "shadow": 6, "text_border": 0, "round": 0.5}
    ex.measure_labels([pill])
    pill["y"] = ex.PILL_Y["bottom"]
    return pill


def visuals(shots, placed, total_s):
    """explainer-style visuals (what launch.caption_checks and full_screen_share read): one per shot and line, each
    with the line being said (or the one before), its clip and the clip's second where it starts."""
    edges = sorted({0.0, total_s, *[s["start"] for s in shots], *[p["start"] for p in placed]})
    out = []
    for a, b in zip(edges, edges[1:]):
        if b - a < 1e-3:
            continue
        shot = next((s for s in shots if s["start"] - 1e-6 <= a < s["end"] - 1e-6), shots[-1])
        line = max([p["line"] for p in placed if p["start"] <= a + 1e-6] or [1])
        out.append({"line": line, "source": str(shot["clip"]), "start_s": round(a, 3), "seconds": round(b - a, 3),
                    "source_start_s": round(a - shot["start"], 3), "kind": "video", "layout": "full",
                    "shot": shot["n"], "rec_s": round(shot["from"] + (a - shot["start"]) * shot.get("speed", 1.0), 3)})
    return out


def build(out_dir, video, shots, lines, spoken, placed, hook, kind, quiet=None, music="auto", seed="short",
          speed=1.2, voice="am_puck", log=print):
    """The finished Short: out_dir/short.mp4, voice.wav, captions.ass, log.json. shots: cutplan shots with "clip"
    (their made clips); placed: cutplan.place_lines. Returns the log."""
    import numpy as np
    out_dir = Path(out_dir)
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    total_frames = sum(s["frames"] for s in shots)
    total_s = total_frames / ex.FPS
    audio, rate, words = voice_track(spoken, placed, total_s)
    ex.save_wav(work / "voice_raw.wav", audio, rate)
    voice_lufs = ex.normalise_voice(work / "voice_raw.wav", out_dir / "voice.wav")
    v = ex.read_wav(out_dir / "voice.wav")
    n = round(total_s * ex.RATE)
    v = np.pad(v, ((0, max(0, n - len(v))), (0, 0)))[:n]

    pill = end_pill()
    events = (ex.open_card_events(hook.upper()) if hook else []) + ex.word_events(words, total_s)
    events += ex.end_pill_events(pill, total_frames)
    (out_dir / "captions.ass").write_text(ex.ass_document(events), encoding="utf-8")
    for font in ex.FONT_FILES:
        shutil.copy(font, work)

    track, note = ex.pick_music(music, ex.ROOT, total_s, seed) if music else (None, None)
    mix = v.copy()
    music_info = {}
    if track:
        bed, music_info = ex.music_bed(track, v, 1.0, work)
        mix += bed
    game, game_info = game_track(video, shots, v, work, quiet)
    if game is not None:
        mix += game
    cuts = [s["start"] for s in shots[1:]]
    pill_click = ex.pill_frames(total_frames)[1] / ex.FPS
    sfx = [{"kind": "whoosh", "at_s": round(c, 3), "why": "a cut"} for c in cuts]
    sfx.append({"kind": "click", "at_s": round(pill_click, 3), "why": "the cursor clicks the subscribe pill"})
    mix += ex.sfx_track(sfx, n)[:, None]
    mixed = work / "mix.f32"
    mix.astype(np.float32).tofile(mixed)
    mix_lufs = ex.normalise([*ex.F32, "-i", mixed], work / "mix.wav", ex.measure_loudness(mixed, ex.F32)[0])

    (work / "shots.txt").write_text("".join(f"file '{Path(s['clip']).resolve().as_posix()}'\n" for s in shots),
                                    encoding="utf-8")
    encoder, _, final_args = ex.pick_encoder()
    ex.ffmpeg(["-f", "concat", "-safe", "0", "-i", "shots.txt", "-i", "mix.wav", "-filter_complex",
               "[0:v]ass=../captions.ass:fontsdir=.,format=yuv420p[p]", "-map", "[p]", "-map", "1:a", "-frames:v",
               total_frames, "-r", ex.FPS, *final_args, *ex.COLOUR_TAGS, "-c:a", "aac", "-b:a", "192k", "-ar", ex.RATE,
               "-ac", "2", "-movflags", "+faststart", "../short.mp4"], cwd=work)
    final = out_dir / "short.mp4"
    video_s = float(ex.probe(final)["format"]["duration"])
    lufs, peak = ex.measure_loudness(final)
    run_log = {"engine": "v4", "title": None, "voice": voice, "speed": speed, "encoder": encoder,
               "durations": {"video_s": round(video_s, 3), "frames": total_frames,
                             "lines": [{"line": p["line"], "start_s": p["start"], "end_s": p["end"]} for p in placed]},
               "word_times": [{"text": w["text"], "start_s": round(w["start"], 3), "end_s": round(w["end"], 3)}
                              for w in words],
               "visuals": visuals(shots, placed, total_s),
               "shots": [{k: (str(s[k]) if k == "clip" else s[k]) for k in ("n", "from", "to", "start", "end", "frames",
                                                                         "clip", "speed", "replay") if k in s}
                         for s in shots],
               "cuts_s": [round(c, 3) for c in cuts], "sfx": sfx,
               "music": {"file": str(track) if track else None, "chosen": note, **music_info},
               "game_audio": game_info,
               "loudness": {"voice_lufs": voice_lufs, "mix_lufs": mix_lufs, "final_lufs": lufs,
                            "final_true_peak_db": peak}}
    (out_dir / "log.json").write_text(json.dumps(run_log, indent=1, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return run_log
