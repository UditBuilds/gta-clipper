"""Runs the test suites, one at a time, each under its own Python (the suites that render need .venv-tts).
Standard library only.

  .\\.venv\\Scripts\\python.exe run_tests.py --quick     while working: logic checks and tiny renders, under 2 minutes
  .\\.venv\\Scripts\\python.exe run_tests.py --full      before calling anything safe to merge: every check
  python run_tests.py --ci                            GitHub: the quick checks that need no GPU, voice or footage

  --only launchtest editortest   just these suites      --installed-ffmpeg   don't use the copy of ffmpeg (below)

One line per suite: checks passed, seconds, interpreter; then a total. Exit code 0 only when every suite said RESULT:
PASS, no check failed, and as many checks passed as are expected (a suite that checks nothing, or loses a check, fails
the run). --quick and --full write their part of docs/test-timings-after.md.

Why a copy of ffmpeg: on the owner's laptop the installed ffmpeg.exe (211 MB) takes about 1.9 s to start, every time, and
the full tier starts it over a thousand times; a byte-identical copy starts in 0.03 s (fastff.py says why). So when the
installed one is slow to start, the suites get the copy production uses (fastff.FOLDER, its SHA-256 checked, made here
when it is missing or the installed file changed) first on their own PATH. Nothing else on the PC changes.

A suite can always be run on its own, and then runs every check:  .\\.venv-tts\\Scripts\\python.exe editortest.py
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import fastff
import heavy
import testkit

HERE = Path(__file__).resolve().parent
VENVS = {v: HERE / v / "Scripts" / "python.exe" for v in (".venv", ".venv-tts")}
CACHE = fastff.FOLDER                     # the fast copy of ffmpeg: the same one production uses
TIMINGS = HERE / "docs" / "test-timings-after.md"
LOGS = Path(tempfile.gettempdir()) / "gta-clipper-tests"
CHECK_LINE = re.compile(r"\s+(PASS|FAIL|SKIP)\s+(.+)")   # how every suite prints a check


def suite(name, venv, checks, tiers=testkit.TIERS):
    """checks: how many the full tier runs (fewer fails the run). tiers: where the suite runs at all; inside it, the
    checks' own tags (testkit.py) decide."""
    return SimpleNamespace(name=name, venv=venv, checks=checks, tiers=tiers)


# Light ones first, so a plain mistake shows in seconds.
SUITES = [
    suite("runnertest", ".venv", 10),
    suite("evaltest", ".venv", 21),
    suite("spreadtest", ".venv", 13),
    suite("vlmtest", ".venv", 39),
    suite("varietytest", ".venv", 14),
    suite("banktest", ".venv", 34),
    suite("indextest", ".venv", 41),
    suite("shorttest", ".venv", 42),
    suite("calibtest", ".venv", 38),
    suite("hooktest", ".venv", 51),
    suite("hudtest", ".venv", 9, ("quick", "full")),         # the owner's recordings
    suite("footagetest", ".venv-tts", 31),
    suite("launchtest", ".venv-tts", 48),
    suite("topictest", ".venv-tts", 24),
    suite("storytest", ".venv-tts", 16),
    suite("editortest", ".venv-tts", 52),
    suite("explainertest", ".venv-tts", 88),
    suite("selftest", ".venv", 3, ("full",)),                # clip.py on a 3-minute video
]
MINIMUM = {"quick": 498, "ci": 483}      # fewer checks than this in all, and the run fails


start_seconds = fastff.start_seconds


def fast_ffmpeg(log=print):
    """The folder to put first on the suites' PATH, or None to use the installed ffmpeg as it is (see the top). The copy
    is the one production uses (fastff.py); it is made here when it is missing."""
    folder = fastff.make(CACHE, log, start_seconds)
    for copy in sorted(Path(folder).glob("ff*.exe")) if folder else []:
        now = min(start_seconds(copy) for _ in range(3))
        log(f"{copy.stem}: a copy that starts in {now:.2f} s ({copy})")
        if now > fastff.SLOW_START_S:
            log(f"WARNING: the copy of {copy.stem} is slow to start too, so the times below are slow ones")
    return folder


def run_suite(s, env, log_file):
    """Runs one suite; returns its name, interpreter, counts, seconds, the checks with their seconds, and problems."""
    out = SimpleNamespace(name=s.name, venv=s.venv, passed=0, failed=0, skipped=0, seconds=0.0, checks=[], problems=[],
                          ok=False)
    python = VENVS[s.venv]
    if not python.is_file():
        out.problems.append(f"{python} is missing (README: how to make {s.venv})")
        return out
    t0 = last = time.perf_counter()
    result, tail = None, []
    with open(log_file, "w", encoding="utf-8") as log:
        p = subprocess.Popen([str(python), "-u", f"{s.name}.py"], cwd=HERE, env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        for raw in p.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            log.write(line + "\n")
            tail = (tail + [line])[-8:]
            now = time.perf_counter()
            m = CHECK_LINE.match(line)
            if m:
                word = {"PASS": "passed", "FAIL": "failed", "SKIP": "skipped"}[m.group(1)]
                setattr(out, word, getattr(out, word) + 1)
                out.checks.append((now - last, m.group(2).split(":")[0][:90]))
                last = now
                if word == "failed":
                    out.problems.append("FAIL " + m.group(2)[:300])
            elif line.startswith("RESULT"):
                result = line
        p.wait()
    out.seconds = time.perf_counter() - t0
    if p.returncode or not (result or "").startswith("RESULT: PASS"):
        out.problems.append(f"exit code {p.returncode}, {result or 'no RESULT line'}")
        out.problems += ["  " + line[:200] for line in tail]
    elif not out.passed:
        out.problems.append("it checked nothing")
    out.ok = not out.problems and not out.failed
    return out


def failures(tier, results, only=False):
    """What makes this run fail, a line each: a suite with a problem; in the full tier a suite with fewer checks passed
    than SUITES says; fewer checks in all than MINIMUM (unless only some suites were asked for)."""
    expected = {s.name: s.checks for s in SUITES}
    out = []
    for r in results:
        if tier == "full" and r.passed < expected.get(r.name, 0) and r.ok:
            r.problems.append(f"{r.passed} checks passed; {expected[r.name]} are expected")
            r.ok = False
        if not r.ok:
            out.append(r.name)
    n = sum(r.passed for r in results)
    if not only and n < MINIMUM.get(tier, 0):
        out.append(f"only {n} checks passed (--{tier} has at least {MINIMUM[tier]})")
    return out


def unlisted(folder=HERE):
    """Suites in the repo that SUITES doesn't know: nobody would run them."""
    return sorted({p.stem for p in folder.glob("*test.py")} - {s.name for s in SUITES})


def write_timings(tier, results, total_s, path=TIMINGS):
    """This tier's part of docs/test-timings-after.md, between its own marks; the other tiers' parts stay."""
    n = sum(r.passed for r in results)
    lines = [f"## --{tier}: {n} checks in {total_s / 60:.1f} min ({date.today():%b %d, %Y})", "",
             "| suite | interpreter | checks | seconds | slowest check |", "|---|---|---:|---:|---|"]
    for r in results:
        slow = max(r.checks, default=(0.0, "-"))
        lines.append(f"| {r.name} | {r.venv} | {r.passed}/{r.passed + r.failed + r.skipped} | {r.seconds:.1f} | "
                     f"{slow[1]} ({slow[0]:.1f} s) |")
    lines += [f"| **all** | | **{n}** | **{total_s:.1f}** | |", "", "Slowest checks:", ""]
    every = sorted(((sec, r.name, name) for r in results for sec, name in r.checks), reverse=True)[:10]
    lines += [f"- {sec:.1f} s {name}: {check}" for sec, name, check in every]
    start, end = f"<!-- {tier} -->", f"<!-- /{tier} -->"
    part = start + "\n" + "\n".join(lines) + "\n" + end
    text = path.read_text(encoding="utf-8") if path.is_file() else (
        "# Test timings after PR 31\n\nWritten by run_tests.py: each tier's part is replaced by its latest complete run. "
        "The seconds of a check that makes something other checks share (a render, a video) hold that too.\n")
    if start in text and end in text:
        text = text[:text.index(start)] + part + text[text.index(end) + len(end):]
    else:
        text = text.rstrip("\n") + "\n\n" + part + "\n"
    path.write_text(text, encoding="utf-8")


def main():
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Runs the test suites, one at a time, each under its own Python.")
    which = ap.add_mutually_exclusive_group(required=True)
    for t in testkit.TIERS:
        which.add_argument(f"--{t}", dest="tier", action="store_const", const=t)
    ap.add_argument("--only", nargs="+", metavar="SUITE", help="just these suites")
    ap.add_argument("--installed-ffmpeg", action="store_true", help="use the installed ffmpeg however slow it starts")
    args = ap.parse_args()
    suites = [s for s in SUITES if args.tier in s.tiers and (not args.only or s.name in args.only)]
    unknown = [n for n in args.only or [] if n not in {s.name for s in suites}]
    if unlisted():
        ap.error(f"add to SUITES: {', '.join(unlisted())}")
    if unknown:
        ap.error(f"no such suite in --{args.tier}: {', '.join(unknown)}")
    if args.tier == "ci":
        os.environ.setdefault("GTA_HEAVY_LOCK", str(Path(tempfile.gettempdir()) / "gta_heavy_job.json"))
        heavy.LOCK = Path(os.environ["GTA_HEAVY_LOCK"])
    heavy.main_guard(f"run_tests.py --{args.tier}")      # one heavy job at a time; the suites inherit it
    LOGS.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, GTA_TEST_TIER=args.tier, PYTHONIOENCODING="utf-8")
    folder = None if args.installed_ffmpeg or args.tier == "ci" else fast_ffmpeg()
    if folder:
        env["PATH"] = str(folder) + os.pathsep + env.get("PATH", "")
    t0, results = time.perf_counter(), []
    for s in suites:
        r = run_suite(s, env, LOGS / f"{args.tier}_{s.name}.log")
        results.append(r)
        failures(args.tier, [r], only=True)
        total = r.passed + r.failed + r.skipped
        print(f"{r.name:14s} {r.passed:3d}/{total:<3d} {r.seconds:7.1f} s  {r.venv:9s} {'' if r.ok else 'FAIL'}".rstrip(),
              flush=True)
        for line in r.problems:
            print(f"    {line}", flush=True)
    total_s = time.perf_counter() - t0
    n, bad = sum(r.passed for r in results), failures(args.tier, results, bool(args.only))
    print(f"TOTAL: {n} checks passed in {len(results)} suites, {total_s / 60:.1f} min ({total_s:.0f} s)"
          + (f"; FAILED: {', '.join(bad)}" if bad else ""))
    if not args.only and args.tier != "ci" and not bad and not args.installed_ffmpeg:
        write_timings(args.tier, results, total_s)
    print(f"RESULT: {'FAIL' if bad else 'PASS'} (logs: {LOGS})")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
