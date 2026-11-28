"""Checks for the test runner (run_tests.py) and the tags (testkit.py): a run that checks nothing, loses a check or breaks
off must fail, on GitHub as well. The suites here are a few lines each, made up in a temporary folder. No ffmpeg needed.

  .venv\\Scripts\\python.exe runnertest.py
"""
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import fastff
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


def with_ffmpeg(fn):
    """fn(tmp, exe) with a made-up installed ffmpeg first on PATH and fastff.FOLDER in tmp; everything is put back."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "bin").mkdir()
        exe = tmp / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        exe.write_bytes(os.urandom(5000))
        exe.chmod(0o755)
        path, checked, folder = os.environ.get("PATH", ""), os.environ.pop(fastff.CHECKED, None), fastff.FOLDER
        os.environ["PATH"] = str(exe.parent)
        fastff.FOLDER = tmp / "tools"
        try:
            return fn(tmp, exe)
        finally:
            os.environ["PATH"], fastff.FOLDER = path, folder
            os.environ.pop(fastff.CHECKED, None)
            if checked is not None:
                os.environ[fastff.CHECKED] = checked


def made_copy(exe):
    """The copy fastff.make makes of a slow made-up ffmpeg."""
    folder = fastff.make(log=lambda m: None, start=lambda p: 1.9 if Path(p) == exe.resolve() else 0.03)
    return folder / exe.name


@check
def production_uses_the_checked_copy_first_on_its_own_path():
    """fastff.use(): a copy whose SHA-256 is the noted one comes first on this process's PATH, so "ffmpeg" is the copy, a
    child process inherits it without hashing it again, and explainer's FFMPEG (looked up when it was imported) points
    at it; nothing is said. The tests make and use the same folder (run_tests.CACHE is fastff.FOLDER), and production
    never makes one by itself"""
    same_folder = run_tests.CACHE == fastff.FOLDER

    def run(tmp, exe):
        copy, said = made_copy(exe), []
        sys.modules["explainer"], old = SimpleNamespace(FFMPEG=str(exe), FFPROBE=None), sys.modules.get("explainer")
        try:
            got = fastff.use(log=said.append)
            named = Path(shutil.which("ffmpeg")).resolve()
            pointed = Path(sys.modules["explainer"].FFMPEG).resolve()
        finally:
            sys.modules.pop("explainer")
            if old is not None:
                sys.modules["explainer"] = old
        keep, fastff.sha = fastff.sha, lambda p: (_ for _ in ()).throw(AssertionError("hashed again"))
        try:
            child = fastff.use(log=said.append)       # as a child process sees it: CHECKED is in its environment
        finally:
            fastff.sha = keep
        return (got == copy.parent == child and named == copy.resolve() == pointed and not said
                and os.environ["PATH"].startswith(str(copy.parent)) and os.environ[fastff.CHECKED] == str(copy.parent)
                and fastff.installed("ffmpeg") == exe.resolve())
    return same_folder and with_ffmpeg(run)


@check
def a_missing_or_wrong_copy_falls_back_to_the_installed_ffmpeg_with_a_warning():
    """no copy, a copy whose bytes changed, a copy with no noted hash, and an installed ffmpeg that was updated since:
    each time PATH stays as it was, the installed ffmpeg answers, and one warning says why and how to make the copy
    again; nothing is copied by use(). make() puts a wrong copy right"""
    def run(tmp, exe):
        got = []

        def fell_back(name):
            said, before = [], os.environ["PATH"]
            ok = (fastff.use(log=said.append) is None and os.environ["PATH"] == before
                  and Path(shutil.which("ffmpeg")).resolve() == exe.resolve() and fastff.CHECKED not in os.environ
                  and len(said) == 1 and said[0].startswith("WARNING") and "fastff.py --make" in said[0])
            got.append((name, ok, said))
        fell_back("missing")
        nothing_made = not fastff.FOLDER.exists()
        copy = made_copy(exe)
        good = copy.read_bytes()
        copy.write_bytes(good[:-1] + bytes([good[-1] ^ 1]))
        fell_back("changed")
        mended = made_copy(exe).read_bytes() == good
        (copy.parent / fastff.MANIFEST).unlink()
        fell_back("no hash")
        made_copy(exe)
        exe.write_bytes(os.urandom(6000))
        fell_back("updated")
        return all(ok for _, ok, _ in got) and nothing_made and mended, got
    good, got = with_ffmpeg(run)
    if not good:
        raise AssertionError(str(got))
    return True


@check
def every_program_that_renders_or_indexes_asks_for_the_fast_ffmpeg():
    """launch, index, footage_short, make_short, tag_media, explainer, edit2, speech, bank and meme_short call
    fastff.use() in main() and only there (importing them changes nothing), and clip.py is left alone"""
    wrong = []
    for name in ("launch", "index", "footage_short", "make_short", "tag_media", "explainer", "edit2", "speech", "bank",
                 "meme_short"):
        text = (run_tests.HERE / f"{name}.py").read_text(encoding="utf-8")
        body = re.split(r"^def main\(.*\):$", text, flags=re.M)[-1].split("\ndef ")[0]
        if text.count("fastff.use(") != 1 or "fastff.use(" not in body:
            wrong.append(name)
    return not wrong and "fastff" not in (run_tests.HERE / "clip.py").read_text(encoding="utf-8")


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


@check
def a_check_that_returns_a_tuple_can_fail():
    """PR 35: a check may return (ok, what it got), but in a suite whose main() asks bool(...) of what comes back that is
    always true, so it can never fail (launchtest's swearing check couldn't). Every suite whose checks return one unpacks it"""
    import ast
    stuck, seen = [], 0
    for f in sorted(run_tests.HERE.glob("*test.py")):
        src = f.read_text(encoding="utf-8")
        for fn in ast.parse(src).body:
            if not isinstance(fn, ast.FunctionDef) or not any(getattr(d, "id", "") == "check" for d in fn.decorator_list):
                continue
            inner = {id(r) for g in ast.walk(fn) if isinstance(g, (ast.FunctionDef, ast.Lambda)) and g is not fn for r in ast.walk(g)}
            if any(isinstance(r, ast.Return) and isinstance(r.value, ast.Tuple) and id(r) not in inner for r in ast.walk(fn)):
                seen += 1
                if "isinstance(got, tuple)" not in src[src.index("\ndef main("):]:
                    stuck.append(f"{f.name}: {fn.name}")
    if stuck:
        raise AssertionError("can never fail: " + ", ".join(stuck))
    return seen > 20


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
