"""Coordination between the instances (Codex and Claude) that work on the same projects.

- Claim (claims/<project>.json): the engine that opened a unit on the project. It lasts until the
  unit closes, including while it is interrupted. An "any" claim is a baton: any engine takes it.
- Run lock (<group>.lock): flock held while the unit runs. The OS releases it if the process dies.
  Projects of the same docker_group share the lock.
- Decisions (decisions.lock and notify.json): one instance at a time talks to ntfy.
"""

import contextlib
import fcntl
import json
import time
from pathlib import Path

from . import paths

SHARED = paths.home() / "shared"
ANY = "any"


def shared_dir(*parts):
    path = SHARED.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def try_lock(name):
    """Exclusive, non-blocking flock on shared/<name>.lock. Returns the open file or None."""
    f = open(shared_dir() / f"{name}.lock", "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return None
    return f


def unlock(handle):
    if handle is not None:
        handle.close()  # closing the file releases the flock


def group_of(project):
    return project.get("docker_group") or project["name"]


@contextlib.contextmanager
def _claims_mutex():
    # Blocking and short: it only serializes reading and writing claims between the instances.
    with open(shared_dir() / "claims.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def _claim_path(project):
    return shared_dir("claims") / f"{project}.json"


def read_claim(project):
    try:
        return json.loads(_claim_path(project).read_text())
    except (OSError, ValueError):
        return None


def _write_claim(project, data):
    path = _claim_path(project)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def claim(project, engine):
    """Claims the project for `engine`, taking over a baton. False if the other engine already claimed it."""
    with _claims_mutex():
        current = read_claim(project)
        if current and current.get("engine") not in (engine, ANY):
            return False
        if not current or current.get("engine") != engine:
            _write_claim(project, {"engine": engine, "since": time.time()})
        return True


def release(project, engine):
    """Removes the claim if it belongs to `engine`."""
    with _claims_mutex():
        current = read_claim(project)
        if current and current.get("engine") == engine:
            _claim_path(project).unlink(missing_ok=True)


def hand_off(project, engine, unit_id=None, setup=None):
    """Turns `engine`'s claim into a baton any engine can take.

    The baton carries the unit id, so the history can join the runs of both engines, and the mode of a
    setup unit ("draft" or "apply"), so the engine that takes it resumes it as one. Old batons have no mode.
    """
    with _claims_mutex():
        current = read_claim(project)
        if current and current.get("engine") not in (engine, ANY):
            return
        data = {"engine": ANY, "handoff_from": engine, "unit_id": unit_id, "since": time.time()}
        if setup:
            data["setup"] = setup
        _write_claim(project, data)


def is_handoff(project):
    current = read_claim(project)
    return bool(current) and current.get("engine") == ANY


def held_by_other(project, engine):
    current = read_claim(project)
    return bool(current) and current.get("engine") not in (engine, ANY)


def reconcile(engine, open_units):
    """Reconciles `engine`'s claims with its state, at startup.

    `open_units` is {project: there is an interrupted unit in the engine's state}. Removes claims
    without an open unit, creates the missing ones and returns the projects with a unit open here
    but claimed by the other engine.
    """
    conflicts = []
    with _claims_mutex():
        for project, is_open in open_units.items():
            current = read_claim(project)
            owner = current.get("engine") if current else None
            if owner == engine and not is_open:
                _claim_path(project).unlink(missing_ok=True)
            elif is_open and owner in (None, ANY):
                _write_claim(project, {"engine": engine, "since": time.time()})
            elif is_open and owner != engine:
                conflicts.append(project)
    return conflicts


def claims():
    """{project: claim} of every current claim."""
    return {p.stem: read_claim(p.stem) for p in sorted(shared_dir("claims").glob("*.json"))}


def load_notify(default):
    try:
        return json.loads((shared_dir() / "notify.json").read_text())
    except (OSError, ValueError):
        return dict(default)


def save_notify(data):
    path = shared_dir() / "notify.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


# --- owner messages (free text via ntfy) ------------------------------------------------


def _notes_path(project):
    return shared_dir("notes") / f"{project}.json"


def _read_notes(project):
    try:
        return json.loads(_notes_path(project).read_text())
    except (OSError, ValueError):
        return []


def _write_notes(project, notes):
    path = _notes_path(project)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(notes, ensure_ascii=False, indent=1))
    tmp.replace(path)


def add_note(project, text):
    """Stores a message for the project's next unit, of either engine."""
    with _claims_mutex():
        notes = _read_notes(project)
        notes.append({"id": f"{time.time_ns()}", "at": time.time(), "text": text, "delivered": False})
        _write_notes(project, notes)


def pending_notes(project):
    return [n for n in _read_notes(project) if not n.get("delivered")]


def mark_delivered(project, ids):
    """Marks as delivered the messages that went into the prompt of a unit that closed."""
    ids = set(ids)
    with _claims_mutex():
        notes = _read_notes(project)
        for n in notes:
            if n["id"] in ids:
                n["delivered"] = True
        _write_notes(project, notes)
