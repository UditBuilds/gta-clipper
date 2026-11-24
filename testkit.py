"""Tags for the test suites' checks: which tier a check is in and what it needs. run_tests.py picks the tier.
Standard library only, so every suite can import it under either Python.

    @check                     a quick check: logic, or a render small enough to run while working
    @check
    @full                      only in the full tier: a real render, the Kokoro voice, a long made-up video
    @check
    @needs("kokoro")           needs something GitHub's machines don't have (NEEDS): left out of the CI tier

A suite run on its own (python explainertest.py) runs every check, as it always has: the tier is "full" unless
run_tests.py sets GTA_TEST_TIER to "quick" (the checks not tagged full) or "ci" (the quick ones that need nothing).
"""
import os

TIERS = ("quick", "full", "ci")
NEEDS = {"kokoro": "the Kokoro voice model (C:\\AI\\gta\\models\\kokoro)",
         "whisper": "the faster-whisper model (C:\\AI\\gta\\models\\whisper)",
         "gta": "the owner's own files under C:\\AI\\gta (recordings, Rockstar's pictures)",
         "nvenc": "an NVIDIA graphics card (h264_nvenc)"}


def tier():
    """The tier this run is for: full unless run_tests.py says otherwise."""
    name = os.environ.get("GTA_TEST_TIER", "full")
    if name not in TIERS:
        raise SystemExit(f"GTA_TEST_TIER is {name!r}; it is one of {', '.join(TIERS)}")
    return name


def full(fn):
    """Tag a check for the full tier only."""
    fn.tier = "full"
    return fn


def needs(*what):
    """Tag a check with what it needs that GitHub's machines don't have: it stays out of the CI tier."""
    unknown = [w for w in what if w not in NEEDS]
    if unknown:
        raise ValueError(f"needs {unknown}; it is one of {', '.join(NEEDS)}")

    def tag(fn):
        fn.needs = what
        return fn
    return tag


def runs_in(fn, name):
    """Whether a check is in a tier."""
    if name == "full":
        return True
    return getattr(fn, "tier", "quick") != "full" and not (name == "ci" and getattr(fn, "needs", ()))


def selected(checks):
    """The checks of this run's tier, in their own order."""
    return [fn for fn in checks if runs_in(fn, tier())]
