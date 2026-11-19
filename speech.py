"""Spoken swearing in a finished Short (launch-fixes, step 7): faster-whisper small.en on the CPU (int8).

The subtitle check (safety.py) only reads burned-in subtitles; the game's own sound in a Short (dialogue, a car radio)
was never checked, so every Short with game sound carried a "listen once" flag. This transcribes the Short and looks
for the same swear words as the subtitle check (safety.PROFANITY). A swear word in the first FIRST_S seconds fails
the Short (the owner's rule: the first 7 s must be clean); later ones are flags.

Runs only where faster-whisper is installed (.venv-tts; approved: small.en, CPU int8, no CUDA wheels). clip.py and
.venv are untouched. Without it, available() is False and render keeps the old "listen once" flag.

  .venv-tts\\Scripts\\python.exe speech.py <short.mp4>        # prints every word with its time, then the hits
"""
import sys
from pathlib import Path

import safety

MODEL = "small.en"
MODEL_DIR = Path(r"C:\AI\gta\models\whisper")
FIRST_S = 7.0
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


def words(path, transcribe=None):
    """[(start_s, end_s, word)] for everything said in the file."""
    if transcribe is None:
        segments, _ = load().transcribe(audio(path), language="en", word_timestamps=True, vad_filter=False,
                                        beam_size=5, condition_on_previous_text=False)
        return [(w.start, w.end, w.word.strip()) for s in segments for w in (s.words or [])]
    return transcribe(path)


def swearing(path, transcribe=None, first_s=FIRST_S):
    """{"words": n, "early": [(t, word)], "later": [(t, word)]}: swear words said before first_s, and after."""
    ws = words(path, transcribe)
    hits = [(round(t0, 2), bad) for t0, _, w in ws for bad in safety.profanity(w)]
    return {"words": len(ws), "early": [h for h in hits if h[0] < first_s],
            "later": [h for h in hits if h[0] >= first_s]}


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    ws = words(sys.argv[1])
    for t0, t1, w in ws:
        print(f"{t0:6.2f}-{t1:6.2f}  {w}")
    hits = [(t0, bad) for t0, _, w in ws for bad in safety.profanity(w)]
    print("SWEARING: " + (", ".join(f"{b!r} at {t:.1f} s" for t, b in hits) if hits else "none"))


if __name__ == "__main__":
    main()
