"""The single configuration file `config.json` in the data folder, written by the panel.

`for_engine` turns it into the legacy shape (what `projects.toml` / `claude.toml` used to parse to),
so the loop keeps reading `cfg["defaults"]`, `cfg["project"]` and so on unchanged. Writes are
read-modify-write under `shared/config.lock` and replace the file atomically.
"""

import contextlib
import copy
import fcntl
import json
import os
import re
import tomllib
from pathlib import Path

import overrides
import schedule

FILE = "config.json"
ENGINES = ("codex", "claude")
VERSION = 1

_COMMON = {
    "enabled": False,
    "orchestrator_effort": "medium",
    "safety_factor": 1.5,
    "primary_cap": 100,
    "initial_cost_primary": 8.0,
    "initial_cost_secondary": 1.5,
    "blocked_cooldown_hours": 6,
    "schedule": None,
}
ENGINE_DEFAULTS = {
    "codex": {**_COMMON, "orchestrator_model": "gpt-6-sol", "max_subagents": 2},
    "claude": {**_COMMON, "orchestrator_model": "claude-opus-5-5", "fable_cap": 80},
}


class ConfigError(ValueError):
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def empty():
    return {
        "version": VERSION,
        "projects": [],
        "engines": copy.deepcopy(ENGINE_DEFAULTS),
        "notify": {"server": "https://ntfy.sh", "decision_timeout_hours": 4},
    }


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _text(v):
    return isinstance(v, str) and bool(v.strip())


def _validate_project(i, p, names, problems):
    where = f"projects[{i}]"
    if not isinstance(p, dict):
        problems.append(f"{where} must be an object")
        return
    name = p.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        problems.append(f"{where}.name must match [A-Za-z0-9_-]+")
    elif name in names:
        problems.append(f"{where}.name {name!r} is duplicated")
    else:
        names.add(name)
    if not _text(p.get("path")):
        problems.append(f"{where}.path must be a non-empty string")
    if "docker_group" in p and not _text(p["docker_group"]):
        problems.append(f"{where}.docker_group must be a non-empty string")
    if "weekly_cap" in p:
        caps = p["weekly_cap"]
        if not isinstance(caps, dict):
            problems.append(f"{where}.weekly_cap must be an object engine -> number")
        else:
            for eng, v in caps.items():
                if not (_num(v) and 0 <= v <= 100):
                    problems.append(f"{where}.weekly_cap.{eng} must be a number between 0 and 100")


def _validate_schedule(where, s, problems):
    if s is None:
        return
    if not isinstance(s, dict):
        problems.append(f"{where} must be null or an object")
        return
    points = []
    for key in ("start", "end"):
        try:
            points.append(schedule.parse(s.get(key)))
        except ValueError as e:
            problems.append(f"{where}.{key}: {e}")
    if len(points) == 2 and points[0] == points[1]:
        problems.append(f"{where}: start and end must differ")
    if not (_num(s.get("start_margin_minutes")) and s["start_margin_minutes"] >= 0):
        problems.append(f"{where}.start_margin_minutes must be a number >= 0")


def _validate_engine(name, e, problems):
    where = f"engines.{name}"
    if not isinstance(e, dict):
        problems.append(f"{where} must be an object")
        return
    if not isinstance(e.get("enabled"), bool):
        problems.append(f"{where}.enabled must be true or false")
    for key in ("orchestrator_model", "orchestrator_effort"):
        if not _text(e.get(key)):
            problems.append(f"{where}.{key} must be a non-empty string")
    sf = e.get("safety_factor")
    if not (_num(sf) and sf > 0):
        problems.append(f"{where}.safety_factor must be a number > 0")
    for key in ("primary_cap", "fable_cap"):
        if key in e or key == "primary_cap":
            if not (_num(e.get(key)) and 0 <= e[key] <= 100):
                problems.append(f"{where}.{key} must be a number between 0 and 100")
    for key in ("initial_cost_primary", "initial_cost_secondary", "blocked_cooldown_hours"):
        if not (_num(e.get(key)) and e[key] >= 0):
            problems.append(f"{where}.{key} must be a number >= 0")
    if "max_subagents" in e:
        m = e["max_subagents"]
        if not (isinstance(m, int) and not isinstance(m, bool) and m >= 1):
            problems.append(f"{where}.max_subagents must be an integer >= 1")
    _validate_schedule(f"{where}.schedule", e.get("schedule"), problems)


def validate(data):
    """The problems found in `data`, as English messages; empty when valid."""
    if not isinstance(data, dict):
        return ["the config must be an object"]
    problems = []
    if data.get("version") != VERSION:
        problems.append(f"version must be {VERSION}")
    projects = data.get("projects")
    if not isinstance(projects, list):
        problems.append("projects must be a list")
    else:
        names = set()
        for i, p in enumerate(projects):
            _validate_project(i, p, names, problems)
    engines = data.get("engines")
    if not isinstance(engines, dict):
        problems.append("engines must be an object")
    else:
        for name, e in engines.items():
            if name not in ENGINES:
                problems.append(f"engines.{name}: unknown engine")
            else:
                _validate_engine(name, e, problems)
    notify = data.get("notify")
    if not isinstance(notify, dict):
        problems.append("notify must be an object")
    else:
        if not isinstance(notify.get("server"), str):
            problems.append("notify.server must be a string")
        t = notify.get("decision_timeout_hours")
        if not (_num(t) and t > 0):
            problems.append("notify.decision_timeout_hours must be a number > 0")
    return problems


def _path(home):
    return Path(home) / FILE


@contextlib.contextmanager
def _lock(home):
    # Blocking and short, like overrides.py.
    shared = Path(home) / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    with open(shared / "config.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def _read_file(home):
    try:
        text = _path(home).read_text()
    except FileNotFoundError:
        return None
    try:
        data = json.loads(text)
    except ValueError as e:
        raise ConfigError([f"{FILE} is not valid JSON: {e}"]) from e
    problems = validate(data)
    if problems:
        raise ConfigError(problems)
    return data


def _write(home, data):
    path = _path(home)
    tmp = path.with_name(f"{FILE}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def _load_locked(home):
    """Like `load`, for a caller that already holds the lock."""
    data = _read_file(home)
    if data is not None:
        return data
    if (Path(home) / "projects.toml").exists():
        data = import_toml(home)
        problems = validate(data)
        if problems:
            raise ConfigError(problems)
        _write(home, data)
        return data
    return empty()


def load(home):
    """The validated config. Imports the TOML files once when `config.json` does not exist yet."""
    data = _read_file(home)
    if data is not None:
        return data
    if not (Path(home) / "projects.toml").exists():
        return empty()
    with _lock(home):
        return _load_locked(home)


def save(home, fn):
    """Read-modify-write under the lock; an invalid result raises `ConfigError` and writes nothing."""
    with _lock(home):
        data = _load_locked(home)
        fn(data)
        problems = validate(data)
        if problems:
            raise ConfigError(problems)
        _write(home, data)
        return data


def for_engine(data, engine):
    """The config in the legacy shape the loop consumes (see `cfg` in autopilot.py)."""
    # The file's values over the defaults: a key missing from a hand-edited file still has a value.
    e = {**copy.deepcopy(ENGINE_DEFAULTS[engine]), **(data["engines"].get(engine) or {})}
    out = {
        "enabled": bool(e.get("enabled")),
        "defaults": {k: v for k, v in e.items() if k not in ("enabled", "schedule")},
        "project": [],
        "engine": {"kind": engine},
        "notify": dict(data["notify"]),
    }
    for p in data["projects"]:
        item = {"name": p["name"], "path": p["path"],
                "weekly_cap": (p.get("weekly_cap") or {}).get(engine, 100)}
        if p.get("docker_group"):
            item["docker_group"] = p["docker_group"]
        out["project"].append(item)
    if engine == "claude":
        out["instance"] = {"name": "claude"}
    if e.get("schedule"):
        out["schedule"] = dict(e["schedule"])
    return out


def _toml(home, name):
    path = Path(home) / name
    if not path.exists():
        return None
    with open(path, "rb") as f:
        return tomllib.load(f)


def import_toml(home):
    """Builds the config from `projects.toml` (Codex) and `claude.toml` (Claude), folding in the
    order and caps of `overrides.json`.

    The overrides file is left as it is: loops started before the change still read those keys,
    and the loops that read `config.json` ignore them.
    """
    data = empty()
    files = {"codex": _toml(home, "projects.toml"), "claude": _toml(home, "claude.toml")}
    by_name = {}
    listed = {eng: set() for eng in ENGINES}
    for eng in ENGINES:
        cfg = files[eng]
        if cfg is None:
            continue
        e = data["engines"][eng]
        e["enabled"] = True
        e.update(cfg.get("defaults") or {})
        if cfg.get("schedule"):
            # schedule.from_config defaults the margin to 15; the config file requires the key.
            e["schedule"] = {"start_margin_minutes": 15, **cfg["schedule"]}
        for p in cfg.get("project") or []:
            listed[eng].add(p["name"])
            item = by_name.get(p["name"])
            if item is None:
                item = {"name": p["name"], "path": p["path"]}
                if p.get("docker_group"):
                    item["docker_group"] = p["docker_group"]
                item["weekly_cap"] = {}
                by_name[p["name"]] = item
                data["projects"].append(item)
            if "weekly_cap" in p:
                item["weekly_cap"][eng] = p["weekly_cap"]
    for item in data["projects"]:
        # A project the other engine's file does not list is not run by that engine.
        for eng in ENGINES:
            if files[eng] is not None and item["name"] not in listed[eng]:
                item["weekly_cap"][eng] = 0
        if not item["weekly_cap"]:
            del item["weekly_cap"]
    primary = next((f for f in (files["codex"], files["claude"]) if f is not None), None)
    notify = dict((primary or {}).get("notify") or {})
    notify.pop("topic", None)
    data["notify"].update(notify)

    ov = overrides.read(home)
    order = ov.get("order")
    if isinstance(order, list):
        rank = {}
        for n in order:
            if isinstance(n, str) and n not in rank:
                rank[n] = len(rank)
        listed = sorted((p for p in data["projects"] if p["name"] in rank), key=lambda p: rank[p["name"]])
        data["projects"] = listed + [p for p in data["projects"] if p["name"] not in rank]
    per_project = ov.get("projects") if isinstance(ov.get("projects"), dict) else {}
    for p in data["projects"]:
        mine = per_project.get(p["name"])
        caps = mine.get("weekly_cap") if isinstance(mine, dict) else None
        for eng, v in (caps.items() if isinstance(caps, dict) else ()):
            if eng in ENGINES and overrides.valid_cap(v):
                p.setdefault("weekly_cap", {})[eng] = v
    primary_caps = ov.get("primary_cap")
    for eng, v in (primary_caps.items() if isinstance(primary_caps, dict) else ()):
        if eng in ENGINES and overrides.valid_cap(v):
            data["engines"][eng]["primary_cap"] = v
    return data
