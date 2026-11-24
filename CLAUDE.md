# gta-clipper

Turns the owner's PS5 GTA 6 recordings into YouTube Shorts. Production path: `/launch` (launch.py, engine v4). Read
README.md for how it works. This file is only what to run and what not to touch.

## Tests

```powershell
.\.venv\Scripts\python.exe run_tests.py --quick     # while working: logic checks and tiny renders, under 2 minutes
.\.venv\Scripts\python.exe run_tests.py --full      # before calling anything safe to merge: every check
```

- Run `--quick` after every change and `--full` before saying "safe to merge". `--only launchtest editortest` runs just
  those suites. GitHub runs `--ci` (the quick checks that need no NVIDIA card, voice model, Ollama or footage) on every
  pull request.
- The runner picks each suite's Python, runs them one at a time (the laptop has little RAM; heavy.py's lock), and fails
  when a check fails, when a suite has no RESULT line, or when fewer checks ran than expected. Read its exit code and
  its last line, not just the PASS lines.
- A new check is quick unless it is tagged `@testkit.full`: tag a real render, the Kokoro voice or a long made-up video.
  Tag what GitHub's machines don't have with `@testkit.needs("kokoro")`, `"whisper"`, `"gta"` (files under C:\AI\gta) or
  `"nvenc"`. When a suite gains checks, raise its number in run_tests.py's SUITES.
- Never remove, skip or weaken a check to make a run pass, and never widen a tolerance.

## Content bank

```powershell
.\.venv\Scripts\python.exe bank.py build                          # index what is new, mine, label, write the bank + page
.\.venv\Scripts\python.exe bank.py posted ID --name "what it was" # the owner posted it: lock its footage, add its topic
.\.venv\Scripts\python.exe bank.py rejected ID                    # the owner said no: never offered again
```

- `C:\AI\gta\bank\content_bank.json` and `bank.html`: every moment of every GTA 6 recording. `/launch` picks from it.
- Footage is locked when it is **posted** (or rejected), never by a render. Only the owner posts: run `posted` or `rejected`
  only when they say so.
- `_used_footage.json` and `posted_topics.json` are the owner's records: copy them before any change, never lose an entry.

## Rules

- `.venv` (Python 3.14) is standard library only and runs clip.py; `.venv-tts` (Python 3.13) has the voice, numpy and
  faster-whisper. No new pip packages. clip.py stays standard library only.
- ffmpeg: every program's main() calls `fastff.use()` first (a SHA-256-checked copy in `C:\AI\tools\ffmpeg`; the installed
  one takes 1.9 s to start). Nothing happens on import. The tests use the same copy.
- One heavy job at a time (heavy.py): renders, Kokoro, Whisper, Ollama. Never work round its lock.
- Never commit recordings or other media; test fixtures stay under 5 MB in all.
- The owner merges. Never merge a PR.
