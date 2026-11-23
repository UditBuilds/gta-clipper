"""Checks for the test runner (run_tests.py) and the tags (testkit.py): a run that checks nothing, loses a check or breaks
off must fail, on GitHub as well. The suites here are a few lines each, made up in a temporary folder. No ffmpeg needed.

  .venv\\Scripts\\python.exe runnertest.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import run_tests
import testkit

CHECKS = []


def check(fn):
    CHECKS.append(fn)
    return fn


def in_tier(name, fn):
    """fn() with GTA_TEST_TIER set to name (None: not set)."""
    old = os.environ.pop("GTA_TEST_TIER", None)
    if name is not None:
        os.environ["GTA_TEST_TIER"] = name
    try:
        return fn()
    finally:
        os.environ.pop("GTA_TEST_TIER", None)
        if old is not None:
            os.environ["GTA_TEST_TIER"] = old


@check
def the_tags_pick_the_checks_of_a_tier():
    """full: every check (also when no tier is set: a suite run on its own); quick: not the ones tagged full; ci: of
    those, not the ones that need what GitHub lacks; a tier or a need that doesn't exist is an error"""
    def plain():
        pass

    @testkit.full
    def big():
        pass

    @testkit.needs("kokoro")
    def voice():
        pass
    checks = [plain, big, voice]
    got = {t: in_tier(t, lambda: testkit.selected(checks)) for t in (None, "full", "quick", "ci")}
    errors = 0
    for bad in (lambda: in_tier("fast", lambda: testkit.selected(checks)), lambda: testkit.needs("luck")):
        try:
            bad()
        except (SystemExit, ValueError):
            errors += 1
    return (got == {None: checks, "full": checks, "quick": [plain, voice], "ci": [plain]} and errors == 2
            and plain() is None)


def made_up_suite(text):
    """run_tests.run_suite on a suite with this text."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "faketest.py").write_text(text, encoding="utf-8")
        here, venvs = run_tests.HERE, run_tests.VENVS
        run_tests.HERE, run_tests.VENVS = Path(tmp), {".venv": Path(sys.executable)}
        try:
            return run_tests.run_suite(run_tests.suite("faketest", ".venv", 2), dict(os.environ), Path(tmp) / "log.txt")
        finally:
            run_tests.HERE, run_tests.VENVS = here, venvs


GOOD = "print('  PASS one: it works')\nprint('  PASS two: so does this')\nprint('RESULT: PASS')\n"


@check
def a_good_suite_passes_and_is_counted():
    """2 PASS lines and RESULT: PASS is a good suite of 2 checks, each with its seconds"""
    r = made_up_suite(GOOD)
    return r.ok and (r.passed, r.failed, r.skipped) == (2, 0, 0) and [n for _, n in r.checks] == ["one", "two"]


@check
def a_bad_suite_fails_whatever_it_prints():
    """a FAIL line, a crash after the PASS lines (no RESULT line), RESULT: PASS with exit code 1, RESULT: FAIL, no check
    at all, and a missing interpreter: each is a failed suite that says why"""
    bad = [GOOD.replace("PASS two", "FAIL two"), GOOD.replace("print('RESULT: PASS')", "raise SystemError('boom')"),
           GOOD + "raise SystemExit(1)\n", GOOD.replace("RESULT: PASS", "RESULT: FAIL"), "print('RESULT: PASS')\n"]
    got = [made_up_suite(t) for t in bad]
    venvs, run_tests.VENVS = run_tests.VENVS, {".venv": Path("nowhere") / "python.exe"}
    try:
        got.append(run_tests.run_suite(run_tests.suite("faketest", ".venv", 2), dict(os.environ), Path("unused")))
    finally:
        run_tests.VENVS = venvs
    why = [" ".join(r.problems) for r in got]
    return (not any(r.ok for r in got) and "FAIL two" in why[0] and "no RESULT line" in why[1] and "boom" in why[1]
            and "exit code 1" in why[2] and "RESULT: FAIL" in why[3] and "checked nothing" in why[4]
            and "is missing" in why[5])


@check
def a_run_with_too_few_checks_fails():
    """--full: a suite with one check fewer than SUITES says fails, with both numbers; --quick and --ci: fewer checks in
    all than MINIMUM fails, unless only some suites were asked for; the real minimums are over 400"""
    def ran(name, passed):
        return SimpleNamespace(name=name, passed=passed, ok=True, problems=[])
    want = {s.name: s.checks for s in run_tests.SUITES}
    lost = [ran(n, k - (n == "explainertest")) for n, k in want.items()]
    whole = [ran(n, k) for n, k in want.items()]
    few = [ran("evaltest", 21)]
    return (run_tests.failures("full", lost) == ["explainertest"] and "are expected" in lost[-2].problems[0]
            and run_tests.failures("full", whole) == [] and run_tests.failures("quick", few, only=True) == []
            and [len(run_tests.failures(t, few)) for t in ("quick", "ci")] == [1, 1]
            and "only 21 checks" in run_tests.failures("ci", few)[0]
            and run_tests.MINIMUM["quick"] > 400 and run_tests.MINIMUM["ci"] > 400)


@check
def every_suite_of_the_repo_is_run_and_none_lost_its_number():
    """every *test.py is in SUITES, under .venv-tts when it uses numpy, and SUITES expects at least as many checks as the
    suite has "@check" lines (raise the number when a suite gains checks)"""
    wrong = list(run_tests.unlisted())
    for s in run_tests.SUITES:
        text = (run_tests.HERE / f"{s.name}.py").read_text(encoding="utf-8")
        if len(re.findall(r"^@check$", text, re.M)) > s.checks:
            wrong.append(f"{s.name} has more checks than SUITES expects")
        if re.search(r"^ *import numpy", text, re.M) and s.venv != ".venv-tts":
            wrong.append(f"{s.name} needs numpy")
    if wrong:
        raise AssertionError("; ".join(wrong))
    return len({s.name for s in run_tests.SUITES}) == len(run_tests.SUITES) >= 17


@check
def a_slow_ffmpeg_gets_an_identical_copy_and_a_fast_one_is_left_alone():
    """fast_ffmpeg with made-up files and start times: a slow ffmpeg is copied byte for byte into the cache (older copies
    go) and its folder comes back; the second run uses the copy without timing the installed one; a fast one is used as
    it is, and nothing is copied"""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "bin").mkdir()
        exe = tmp / "bin" / "ffmpeg.exe"
        exe.write_bytes(os.urandom(5000))
        (tmp / "cache" / "ffmpeg-1-1").mkdir(parents=True)
        timed = []
        keep = run_tests.CACHE, run_tests.start_seconds, run_tests.shutil.which
        run_tests.CACHE = tmp / "cache"
        run_tests.shutil.which = lambda name: str(exe) if name == "ffmpeg" else None
        try:
            def slow(path):
                timed.append(Path(path))
                return 1.9 if Path(path) == exe.resolve() else 0.03
            run_tests.start_seconds = slow
            first = run_tests.fast_ffmpeg(log=lambda m: None)
            same = (first / "ffmpeg.exe").read_bytes() == exe.read_bytes()
            left = sorted(p.name for p in (tmp / "cache").iterdir())
            timed.clear()
            second = run_tests.fast_ffmpeg(log=lambda m: None)
            again = exe.resolve() in timed
            run_tests.CACHE, run_tests.start_seconds = tmp / "cache2", lambda path: 0.03
            fast = run_tests.fast_ffmpeg(log=lambda m: None)
        finally:
            run_tests.CACHE, run_tests.start_seconds, run_tests.shutil.which = keep
        return (same and left == [first.name] and second == first and not again and fast is None
                and not (tmp / "cache2").exists())


@check
def the_timings_file_keeps_the_other_tiers_part():
    """write_timings replaces only its own tier's part of the file, however often it runs"""
    def result(k):
        return [SimpleNamespace(name="evaltest", venv=".venv", passed=k, failed=0, skipped=0, seconds=1.0,
                                checks=[(0.5, "one_check")])]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.md"
        for tier, k in (("quick", 11), ("full", 22), ("quick", 33)):
            run_tests.write_timings(tier, result(k), 60.0, path)
        text = path.read_text(encoding="utf-8")
    return (text.count("## --quick") == text.count("## --full") == 1 and "33 checks" in text and "22 checks" in text
            and "11 checks" not in text and "one_check" in text)


def main():
    ok = True
    for fn in testkit.selected(CHECKS):
        try:
            good, err = bool(fn()), ""
        except Exception as e:
            good, err = False, f"  ({type(e).__name__}: {e})"
        ok &= good
        print(f"  {'PASS' if good else 'FAIL'} {fn.__name__}: {' '.join((fn.__doc__ or '').split())}{err}", flush=True)
    print(f"RESULT: {'PASS' if ok else 'FAIL'} ({len(testkit.selected(CHECKS))} checks)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
