"""Panel requests the loops act on: `overrides.json` in the data folder.

The panel writes it, the loops read it on every turn. Writes are read-modify-write under
`shared/overrides.lock` and replace the file atomically.
"""

import contextlib
import copy
import fcntl
import json
import os
import time
from pathlib import Path

FILE = "overrides.json"


def _path(home):
    return Path(home) / FILE


@contextlib.contextmanager
def _lock(home):
    # Blocking and short, like the claims mutex in coord.py.
    shared = Path(home) / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    with open(shared / "overrides.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def read(home):
    """The parsed overrides, or {} when missing, unreadable, invalid or not an object."""
    try:
        data = json.loads(_path(home).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(home, data):
    path = _path(home)
    tmp = path.with_name(f"{FILE}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def update(home, fn):
    """Read-modify-write under the lock: `fn(data)` mutates in place; `poke` is always refreshed."""
    with _lock(home):
        data = read(home)
        fn(data)
        data["poke"] = time.time()
        _write(home, data)
        return data


def take_next(home, project):
    """Removes `next` if it is `project`. True if it was removed. Does not touch `poke`."""
    with _lock(home):
        data = read(home)
        if data.get("next") != project:
            return False
        del data["next"]
        _write(home, data)
        return True


def valid_cap(value):
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100
    return ok


def _dict(value):
    return value if isinstance(value, dict) else {}


def apply(cfg, engine_name, ov):
    """A copy of `cfg` with the order, caps and a `paused` flag per project applied. Never raises."""
    out = copy.deepcopy(cfg)
    ov = _dict(ov)
    projects = out.get("project") or []
    order = ov.get("order")
    if isinstance(order, list):
        rank = {}
        for name in order:
            if isinstance(name, str) and name not in rank:
                rank[name] = len(rank)
        listed = sorted((p for p in projects if p.get("name") in rank), key=lambda p: rank[p["name"]])
        rest = [p for p in projects if p.get("name") not in rank]
        projects = listed + rest
    per_project = _dict(ov.get("projects"))
    for p in projects:
        mine = _dict(per_project.get(p.get("name")))
        p["paused"] = mine.get("paused") is True
        cap = _dict(mine.get("weekly_cap")).get(engine_name)
        if valid_cap(cap):
            p["weekly_cap"] = cap
    out["project"] = projects
    primary = _dict(ov.get("primary_cap")).get(engine_name)
    if valid_cap(primary):
        out.setdefault("defaults", {})["primary_cap"] = primary
    return out


def resets(ov):
    """The `reset` map with only numeric timestamps."""
    return {
        k: v
        for k, v in _dict(_dict(ov).get("reset")).items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }


_cap = valid_cap  # old internal name
