"""Setup requests: preparing a project for the method (`<home>/setup/<project>.json`).

The panel creates a request and moves it (approve, ask for changes, cancel); the loops move it when
a setup unit closes. Writes are read-modify-write under `shared/setup.lock` and replace the file
atomically, like `overrides.py`. A missing or invalid file means no setup.

Also here: reading the draft the agent writes in `<project>/.superpowers/autopilot/setup/` (never
through a symlink), and the one change turno itself makes in a project: `.superpowers/` in the
clone's `.git/info/exclude`.
"""

import contextlib
import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

FOLDER = "setup"
DRAFT = Path(".superpowers") / "autopilot" / "setup"
STATUSES = ("requested", "review", "approved", "done")
OPEN = ("requested", "review", "approved")
STAMPS = {"requested": "requested_at", "review": "drafted_at", "approved": "approved_at", "done": "done_at"}
MAX_FILE = 200 * 1024
MAX_FILES = 50
FIRST = ("notes.md", "autopilot.md")
IGNORE_LINES = (".superpowers/", ".superpowers", "/.superpowers/", "/.superpowers")


def _path(home, name):
    return Path(home) / FOLDER / f"{name}.json"


@contextlib.contextmanager
def _lock(home):
    # Blocking and short, like overrides.lock.
    shared = Path(home) / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    with open(shared / "setup.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def _valid(data):
    return isinstance(data, dict) and data.get("status") in STATUSES and isinstance(data.get("goal"), str)


def read(home, name):
    """The request, or None when it is missing, unreadable or invalid."""
    try:
        data = json.loads(_path(home, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if _valid(data) else None


def phase(home, name):
    """The request's status, or None when there is no valid request."""
    r = read(home, name)
    return r["status"] if r else None


def new_request(goal, now=None):
    return {"goal": goal, "status": "requested", "feedback": None, "requested_at": now or time.time(),
            "drafted_at": None, "approved_at": None, "done_at": None}


def _write(home, name, data):
    path = _path(home, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{name}.json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def update(home, name, fn):
    """Read-modify-write under the lock. `fn(current)` gets the request (None when there is none) and
    returns the new one; None writes nothing. An exception from `fn` writes nothing and propagates."""
    with _lock(home):
        new = fn(read(home, name))
        if new is not None:
            _write(home, name, new)
        return new


def transition(home, name, frm, to, **fields):
    """Moves the request from `frm` to `to`, stamping the time, only if it is still in `frm`. True if it moved."""
    def fn(cur):
        if not cur or cur["status"] != frm:
            return None
        return {**cur, **fields, "status": to, STAMPS[to]: time.time()}
    return update(home, name, fn) is not None


def cancel(home, name):
    """Deletes the request unless it is `approved`. Returns its status before, or None when there was none."""
    with _lock(home):
        cur = read(home, name)
        if cur and cur["status"] != "approved":
            _path(home, name).unlink(missing_ok=True)
        return cur["status"] if cur else None


def delete(home, name):
    """Removes the request file. Without one it touches nothing, not even the lock file."""
    if not _path(home, name).exists():
        return
    with _lock(home):
        _path(home, name).unlink(missing_ok=True)


def _draft_root(path):
    """The draft folder, resolved; None when it is missing or any part of it is a symlink leaving the project."""
    try:
        project = Path(path).resolve(strict=True)
        real = (Path(path) / DRAFT).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return real if real == project / DRAFT and real.is_dir() else None


def draft_files(path):
    """The regular files in the draft folder, as POSIX paths relative to it: `notes.md`, `autopilot.md`,
    then the rest sorted. Symlinks are skipped and never followed; at most MAX_FILES."""
    root = _draft_root(path)
    if root is None:
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
        for f in filenames:
            full = Path(dirpath) / f
            if not full.is_symlink() and full.is_file():
                found.append(full.relative_to(root).as_posix())
    found.sort(key=lambda rel: (FIRST.index(rel) if rel in FIRST else len(FIRST), rel))
    return found[:MAX_FILES]


def read_drafts(path):
    """[{path, size, text}] for the panel. `text` is None for a file over MAX_FILE; never reads through a symlink."""
    root = _draft_root(path)
    if root is None:
        return []
    out = []
    for rel in draft_files(path):
        full = root / rel
        try:
            if root not in full.resolve(strict=True).parents:
                continue  # a folder on the way was swapped for a symlink after the listing
            fd = os.open(full, os.O_RDONLY | os.O_NOFOLLOW)
        except (OSError, RuntimeError):
            continue
        with os.fdopen(fd, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            data = f.read(MAX_FILE + 1)
        out.append({"path": rel, "size": size,
                    "text": None if len(data) > MAX_FILE else data.decode("utf-8", "replace")})
    return out


def ensure_excluded(path):
    """Appends `.superpowers/` to the clone's `.git/info/exclude` unless git already ignores that folder
    (local to the clone, never committed). True when it wrote the line. Never raises; without git it does nothing."""
    def git(*args):
        return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=10)
    try:
        if git("check-ignore", "-q", (DRAFT / "notes.md").as_posix()).returncode == 0:
            return False
        r = git("rev-parse", "--path-format=absolute", "--git-path", "info/exclude")
        if r.returncode != 0 or not r.stdout.strip():
            return False
        exclude = Path(r.stdout.strip())
        text = exclude.read_text() if exclude.exists() else ""
        if any(line.strip() in IGNORE_LINES for line in text.splitlines()):
            return False
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a") as f:
            f.write(("" if not text or text.endswith("\n") else "\n") + ".superpowers/\n")
        return True
    except (OSError, subprocess.SubprocessError):
        return False
