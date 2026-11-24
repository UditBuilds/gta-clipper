"""Which ffmpeg runs: one setting for production and for the tests (PR 32). Standard library only.

  .\\.venv\\Scripts\\python.exe fastff.py            which ffmpeg is used, and how long it takes to start
  .\\.venv\\Scripts\\python.exe fastff.py --make     make the copy (run_tests.py makes it by itself when it is missing)

Why a copy: on the owner's laptop something looks through the installed ffmpeg.exe (211 MB, the real file, no shortcut)
again at every start, about 1.9 s each time before ffmpeg does anything (its own work is 0.08 s); ffprobe.exe next to it
starts in 0.02 s. A byte-identical copy in another folder starts in 0.03 s. A v4 Short starts ffmpeg about 60 times.

The copy is kept in FOLDER, in a folder named after the installed file's size and time, with its SHA-256 in sha256.json.
use() puts that folder first on this process's own PATH (its child processes inherit it), so every command stays as it
is, apart from which ffmpeg.exe answers to "ffmpeg". Nothing outside this process changes. When the copy is missing, its
hash is wrong, or the installed ffmpeg was updated since, the installed one is used as before and a warning says so.
Only a program's main() calls use(): importing a module changes nothing.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

FOLDER = Path(os.environ.get("GTA_FFMPEG", r"C:\AI\tools\ffmpeg"))
MANIFEST = "sha256.json"
CHECKED = "GTA_FFMPEG_CHECKED"   # the folder a parent process already checked: its children don't hash it again
SLOW_START_S = 0.5               # an ffmpeg that takes this long to say its version is being looked through at every start
NAMES = ("ffmpeg", "ffprobe")
MAKE = r".\.venv\Scripts\python.exe fastff.py --make"


def start_seconds(exe):
    t = time.perf_counter()
    subprocess.run([str(exe), "-version"], capture_output=True)
    return time.perf_counter() - t


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(2 ** 20):
            h.update(block)
    return h.hexdigest()


def installed(name, folder=None):
    """The installed program as a real path (links followed), or None: the first on PATH that isn't a copy of ours."""
    ours = Path(folder or FOLDER).resolve()
    found = shutil.which(name)
    if found and ours in Path(found).resolve().parents:
        rest = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and ours not in Path(p).resolve().parents]
        found = shutil.which(name, path=os.pathsep.join(rest))
    return Path(found).resolve() if found else None


def copy_folder(exe, folder=None):
    """Where the copies of this installed ffmpeg belong: a new folder when the installed file is another one."""
    info = exe.stat()
    return Path(folder or FOLDER) / f"ffmpeg-{info.st_size}-{info.st_mtime_ns}"


def read_manifest(folder):
    try:
        data = json.loads((Path(folder) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def good_copy(copy):
    """Whether a copy is there and its SHA-256 is the one noted when it was made."""
    noted = read_manifest(copy.parent).get(copy.name)
    return bool(noted) and copy.is_file() and sha(copy) == noted


def make(folder=None, log=print, start=start_seconds):
    """The folder with the copies, made where they are missing or wrong, or None when the installed programs start fast
    (nothing is copied then). Copies of an older installed ffmpeg go."""
    base = Path(folder or FOLDER)
    used = None
    for name in NAMES:
        exe = installed(name, base)
        if exe is None:
            continue
        mine = copy_folder(exe, base)
        copy = mine / exe.name
        if not good_copy(copy):
            was = min(start(exe) for _ in range(2))
            if was < SLOW_START_S:
                continue
            for old in base.glob("ffmpeg-*"):
                if old != mine:
                    shutil.rmtree(old, ignore_errors=True)
            mine.mkdir(parents=True, exist_ok=True)
            part = copy.with_suffix(".part")
            shutil.copy(exe, part)
            digest = sha(exe)
            if sha(part) != digest:
                part.unlink()
                log(f"WARNING: the copy of {name} differs from the installed one; using the installed one")
                continue
            part.replace(copy)
            (mine / MANIFEST).write_text(json.dumps(dict(read_manifest(mine), **{copy.name: digest}), indent=1),
                                         encoding="utf-8")
            start(copy)                      # the first start is looked through, like any new file's
            log(f"{name}: the installed one takes {was:.2f} s to start, so a copy of it is used ({copy})")
        used = mine
    return used


def find(folder=None):
    """(the folder with a checked copy of the installed ffmpeg, None) or (None, why there is none to use)."""
    exe = installed("ffmpeg", folder)
    if exe is None:
        return None, "ffmpeg is not on PATH"
    mine = copy_folder(exe, folder)
    copy = mine / exe.name
    if not copy.is_file():
        return None, f"there is no copy of it in {mine.parent} (make one: {MAKE})"
    if not good_copy(copy):
        return None, f"the copy's SHA-256 is not the one noted when it was made: {copy} (make it again: {MAKE})"
    return mine, None


def use(log=print, folder=None):
    """Put the checked copy first on this process's PATH; returns its folder. Without one the installed ffmpeg is used,
    with a warning, and None comes back. Every program's main() calls this before anything else."""
    checked = os.environ.get(CHECKED)
    if checked and Path(checked).is_dir():          # the parent process checked it and it is on our PATH
        mine = Path(checked)
    else:
        mine, why = find(folder)
        if mine is None:
            log(f"WARNING: using the installed ffmpeg, which can be slow to start: {why}")
            return None
        os.environ["PATH"] = str(mine) + os.pathsep + os.environ.get("PATH", "")
        os.environ[CHECKED] = str(mine)
    explainer = sys.modules.get("explainer")        # it looks ffmpeg up when it is imported
    if explainer is not None and hasattr(explainer, "FFMPEG"):
        explainer.FFMPEG, explainer.FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")
    return mine


def main():
    ap = argparse.ArgumentParser(description="Which ffmpeg runs, and how long it takes to start.")
    ap.add_argument("--make", action="store_true", help=f"make the copy in {FOLDER}")
    args = ap.parse_args()
    if args.make:
        make()
    for name in NAMES:
        exe = installed(name)
        print(f"installed {name}: " + (f"{min(start_seconds(exe) for _ in range(2)):.2f} s to start  {exe}" if exe else "none"))
    mine = use()
    if mine:
        for name in NAMES:
            exe = shutil.which(name)
            print(f"used {name}: {min(start_seconds(exe) for _ in range(3)):.2f} s to start  {exe}")


if __name__ == "__main__":
    main()
