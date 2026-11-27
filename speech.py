"""Spoken swearing in a finished Short (launch-fixes, step 7) and the first words it says (PR 34): faster-whisper small.en
on the CPU (int8).

The subtitle check (safety.py) only reads burned-in subtitles; the game's own sound in a Short (dialogue, a car radio)
was never checked, so every Short with game sound carried a "listen once" flag. This transcribes the Short and looks
for the same swear words as the subtitle check (safety.PROFANITY). A swear word in the first FIRST_S seconds fails
the Short (the owner's rule: the first 7 s must be clean); later ones are flags. The first 2 s are transcribed on their
own too: when the first 3 words heard are not the hook's, the Short fails ("Can you ..." is heard as "And you ...").

Runs only where faster-whisper is installed (.venv-tts; approved: small.en, CPU int8, no CUDA wheels). clip.py and
.venv are untouched. Without it, available() is False and render keeps the old "listen once" flag.

  .venv-tts\\Scripts\\python.exe speech.py <short.mp4>   # every word with its time, the words a minute, then the hits
"""
import re
import sys
from pathlib import Path

import safety

MODEL = "small.en"
MODEL_DIR = Path(r"C:\AI\gta\models\whisper")
FIRST_S = 7.0
FIRST_WORDS_S, FIRST_WORDS = 2.0, 3      # the hook check: this many words, heard in the first seconds
DIGITS = {w: str(n) for n, w in enumerate("zero one two three four five six seven eight nine ten".split())}
PAUSE_S = 0.1                # a gap between two words this long is a pause (assemble leaves 0.12 s between sentences,
                             # cutplan 0.12 s or more between lines); a shorter one is inside the talking
_model = None


def available():
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def load(model=MODEL):
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        _model = WhisperModel(model, device="cpu", compute_type="int8", download_root=str(MODEL_DIR))
    return _model


def audio(path, rate=16000):
    """The file's sound as 16 kHz mono float32 (numpy), decoded by ffmpeg. faster-whisper's own decoder (PyAV) breaks
    with PyAV 19 (an argument it passes was removed), and ffmpeg is what every other step uses."""
    import subprocess
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le",
                          "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def words(path, transcribe=None, seconds=None):
    """[(start_s, end_s, word)] for everything said in the file, or in its first `seconds` alone."""
    if transcribe is None:
        sound = audio(path)
        sound = sound if seconds is None else sound[:int(seconds * 16000)]
        segments, _ = load().transcribe(sound, language="en", word_timestamps=True, vad_filter=False,
                                        beam_size=5, condition_on_previous_text=False)
        return [(w.start, w.end, w.word.strip()) for s in segments for w in (s.words or [])]
    return transcribe(path)


def wpm(ws):
    """The talking speed of [(start_s, end_s, word)], two ways (Nov 23; PR 30's test said 262 for lines glued 0.12 s
    apart, which no Short sounds like):
      wpm_with_pauses   words / (the last word's end - the first word's start): what a viewer hears
      wpm_voiced_only   words / the seconds someone is talking, every pause of PAUSE_S or more left out
    A number only, never a reason to fail a Short. None where nothing was said."""
    ws = sorted(w for w in ws if any(c.isalnum() for c in w[2]))
    out = {"words": len(ws), "span_s": None, "voiced_s": None, "wpm_with_pauses": None, "wpm_voiced_only": None}
    if not ws:
        return out
    pauses, end = 0.0, ws[0][1]
    for a, b, _ in ws[1:]:
        if a - end >= PAUSE_S - 1e-6:
            pauses += a - end
        end = max(end, b)
    span = end - ws[0][0]
    voiced = span - pauses
    if voiced <= 0:
        return out
    return dict(out, span_s=round(span, 2), voiced_s=round(voiced, 2), wpm_with_pauses=round(len(ws) / span * 60, 1),
                wpm_voiced_only=round(len(ws) / voiced * 60, 1))


def first_words(path):
    """The words heard in the file's first FIRST_WORDS_S seconds, transcribed on their own."""
    return [w for _, _, w in words(path, seconds=FIRST_WORDS_S)]


def plain(text):
    """A text's words for comparing: lower case, no punctuation ("don't" is "dont"), number words as digits."""
    low = str(text or "").lower().replace("’", "").replace("'", "")
    return [DIGITS.get(w, w) for w in re.findall(r"[a-z0-9]+", low)]


def first_words_problem(say, heard):
    """Why a Short's first words are not its hook's, or None. say: line 1 as written; heard: the words Whisper heard at the
    start. The first FIRST_WORDS are compared (a hook of fewer words: all of them)."""
    want = plain(say)[:FIRST_WORDS]
    got = plain(" ".join(heard))[:len(want)]
    if got == want:
        return None
    return (f"the hook is misheard: the first words are \"{' '.join(want)}\" but Whisper hears \"{' '.join(got)}\"; a viewer "
            "may too. Reword line 1 and test the new words on the voice alone before rendering: an opener can be heard "
            "right with one verb and wrong with another (\"Can anyone take\" held on Nov 25, \"Can anyone steal\" didn't)")


def swearing(path, transcribe=None, first_s=FIRST_S):
    """{"words": n, "early": [(t, word)], "later": [(t, word)]}: swear words said before first_s, and after."""
    ws = words(path, transcribe)
    hits = [(round(t0, 2), bad) for t0, _, w in ws for bad in safety.profanity(w)]
    return {"words": len(ws), "early": [h for h in hits if h[0] < first_s],
            "later": [h for h in hits if h[0] >= first_s]}


def main():
    import fastff
    fastff.use()
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    ws = words(sys.argv[1])
    for t0, t1, w in ws:
        print(f"{t0:6.2f}-{t1:6.2f}  {w}")
    r = wpm(ws)
    if r["wpm_with_pauses"] is not None:
        print(f"WORDS A MINUTE: {r['wpm_with_pauses']:.0f} with the pauses ({r['words']} words in {r['span_s']:.1f} s); "
              f"{r['wpm_voiced_only']:.0f} voiced only, pauses left out ({r['voiced_s']:.1f} s of talking)")
    hits = [(t0, bad) for t0, _, w in ws for bad in safety.profanity(w)]
    print("SWEARING: " + (", ".join(f"{b!r} at {t:.1f} s" for t, b in hits) if hits else "none"))


if __name__ == "__main__":
    main()
