"""One heavy job at a time on this laptop (launch-fixes, Nov 19).

Launch night's prepare was stopped twice for low memory: once the safety step had no memory guard, and once a
prepare ran while other heavy work (test suites, renders) shared the 24 GB. Every heavy entry point (launch.py
prepare and render, index.py, tag_media.py, footage_short.py and the test suites that render) takes this lock
first; a second one refuses with a plain line naming the job that holds it.

The lock is a small JSON file: {"pid", "what", "started", "token", "created"}. A lock whose process is gone (or whose
pid now belongs to a newer process) is stale and is taken over. A child process of the holder (launch.py render runs
explainer.py, say) inherits GTA_HEAVY_JOB=<token> and passes; so does a nested job() in the same process.
Standard library only.
"""
import ctypes
import json
import os
import secrets
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

LOCK = Path(os.environ.get("GTA_HEAVY_LOCK", r"C:\AI\gta\_heavy_job.json"))
ENV = "GTA_HEAVY_JOB"


class Busy(Exception):
    """Another heavy job holds the lock."""


def free_ram_gb():
    """Free physical memory in GB (Windows); None where it can't be read."""
    class Status(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("avail", ctypes.c_ulonglong), ("a", ctypes.c_ulonglong), ("b", ctypes.c_ulonglong),
                    ("c", ctypes.c_ulonglong), ("d", ctypes.c_ulonglong), ("e", ctypes.c_ulonglong)]
    try:
        st = Status()
        st.length = ctypes.sizeof(Status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        return st.avail / 2 ** 30
    except (AttributeError, OSError):
        return None


def ram_line(free=None):
    free = free_ram_gb() if free is None else free
    return "Free memory: " + (f"{free:.1f} GB" if free is not None else "unknown")


def process_created(pid):
    """The process's creation time (seconds since 1970), or None when no such process is running."""
    try:
        k32 = ctypes.windll.kernel32
    except AttributeError:                       # not Windows: assume alive when the pid exists
        try:
            os.kill(pid, 0)
            return 0.0
        except OSError:
            return None
    handle = k32.OpenProcess(0x1000, False, int(pid))      # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:   # 259 = STILL_ACTIVE
            return None
        times = [ctypes.c_ulonglong() for _ in range(4)]
        if not k32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return 0.0
        return times[0].value / 1e7 - 11644473600.0         # FILETIME (100 ns since 1601) -> Unix seconds
    finally:
        k32.CloseHandle(handle)


def holder(lock=None, alive=process_created):
    """The live lock's contents, or None when there is no lock or it is stale."""
    lock = Path(lock or LOCK)
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "pid" not in data:
        return None
    created = alive(data["pid"])
    if created is None:
        return None
    # A reused pid: the running process started after the lock was written.
    if created and data.get("created") and created > data["created"] + 2.0:
        return None
    return data


def acquire(what, lock=None, alive=process_created, pid=None, env=None):
    """Take the lock for `what`; returns the token, or None when this process (or its parent job) already holds it.
    Raises Busy when another live job holds it."""
    lock, env = Path(lock or LOCK), os.environ if env is None else env
    pid = os.getpid() if pid is None else pid
    held = holder(lock, alive)
    if held:
        if held["pid"] == pid or (held.get("token") and env.get(ENV) == held["token"]):
            return None
        raise Busy(f"another heavy job is running: {held.get('what', '?')} (process {held['pid']}, since "
                   f"{held.get('started', '?')}). One heavy job at a time on this laptop: wait for it to finish, "
                   f"then run this again. If that process is really gone, delete {lock}")
    token = secrets.token_hex(8)
    lock.parent.mkdir(parents=True, exist_ok=True)
    tmp = lock.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": pid, "what": what, "token": token,
                               "started": datetime.now().isoformat(timespec="seconds"),
                               "created": alive(pid) or time.time()}), encoding="utf-8")
    os.replace(tmp, lock)
    env[ENV] = token
    return token


def release(token, lock=None, env=None):
    lock, env = Path(lock or LOCK), os.environ if env is None else env
    if token is None:
        return
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict) and data.get("token") == token:
        try:
            lock.unlink()
        except OSError:
            pass
    if env.get(ENV) == token:
        env.pop(ENV, None)


@contextmanager
def job(what, lock=None, log=print, alive=process_created):
    """with heavy.job("launch prepare"): ... -- prints free memory, refuses (Busy) while another heavy job runs."""
    token = acquire(what, lock, alive)
    try:
        if token and log:
            log(f"{ram_line()} (start of {what})")
        yield token
    finally:
        release(token, lock)


def main_guard(what):
    """For a script's __main__: take the lock or print the reason and exit 3. Released when the process exits."""
    import atexit
    try:
        token = acquire(what)
    except Busy as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(3)
    if token:
        print(f"{ram_line()} (start of {what})", flush=True)
        atexit.register(release, token)
    return token
