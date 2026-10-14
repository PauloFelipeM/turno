"""What the panel's write routes do, as plain functions: each returns (status, body) or raises ApiError.

They only touch `config.json` (through `config.save`, which validates before writing), `overrides.json`
(under its lock), decision answers (under the decisions lock), owner notes (through `coord.add_note`),
and start/stop the loops or signal them. They never edit state files or the projects, and removing a
project only drops its entry from the config.
"""

import copy
import os
import signal
import threading
import time
import traceback
from pathlib import Path

import autopilot
import config
import coord
import engines
import overrides
import panel

MAX_BODY = 64 * 1024
LOCK_WAIT = 5.0  # seconds answering a decision waits for shared/decisions.lock


class ApiError(Exception):
    def __init__(self, status, code, message=""):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def _dict(x):
    return x if isinstance(x, dict) else {}


def bad(message):
    return ApiError(400, "bad_request", message)


def _number(x):
    return overrides.valid_cap(x)


def _known(home):
    """(project names in config order, engines that have an instance)."""
    names, kinds = [], []
    for inst in panel.configured(home):
        kinds.append(inst["engine"])
        names += [p["name"] for p in inst["cfg"]["project"] if p["name"] not in names]
    return names, kinds


def _need_project(home, name):
    if name not in _known(home)[0]:
        raise ApiError(404, "not_found", f"unknown project: {name}")


def _background(fn, engine):
    def run():
        try:
            fn([engine])
        except Exception:
            traceback.print_exc()
    threading.Thread(target=run, daemon=True).start()


def engine_action(home, engine, action):
    insts = panel.instances(home)
    if any("error" in i for i in insts):
        raise ApiError(400, "config", "config.json is invalid; fix it before starting or stopping a loop")
    inst = next((i for i in insts if i["engine"] == engine), None)
    if inst is None:
        raise ApiError(404, "not_found", f"unknown engine: {engine}")
    running = panel.loop_pid(home, engine) is not None
    if action == "start":
        if not inst["enabled"]:
            raise ApiError(409, "disabled", "the engine is disabled in the configuration")
        if running:
            raise ApiError(409, "already_running", "the loop is already running")
        _background(autopilot.start_loops, engine)
    else:
        if not running:
            raise ApiError(409, "not_running", "the loop is not running")
        _background(autopilot.stop_loops, engine)
    return 202, {"ok": True}


def _project_entry(d, name):
    """d["projects"][name], replacing anything that is not an object (a corrupt file) with {}."""
    if not isinstance(d.get("projects"), dict):
        d["projects"] = {}
    if not isinstance(d["projects"].get(name), dict):
        d["projects"][name] = {}
    return d["projects"][name]


def _set_project(home, name, **fields):
    overrides.update(home, lambda d: _project_entry(d, name).update(fields))


def project_action(home, name, action):
    _need_project(home, name)
    if action in ("pause", "resume"):
        _set_project(home, name, paused=action == "pause")
    elif action == "next":
        def fn(d):
            if _project_entry(d, name).get("paused") is True:
                raise ApiError(409, "paused", "the project is paused")  # under the lock: no race with pause
            d["next"] = name
        overrides.update(home, fn)
    elif action == "reset":
        def fn(d):
            if not isinstance(d.get("reset"), dict):
                d["reset"] = {}
            d["reset"][name] = time.time()
        overrides.update(home, fn)
    elif action == "interrupt":
        return interrupt(home, name)
    else:
        raise ApiError(404, "not_found")
    return 200, {"ok": True}


def _running_pid(home, name):
    for inst in panel.configured(home):
        state = panel.read_json(inst["state"]) or {}
        loop = _dict(state.get("_loop"))
        if loop.get("phase") == "running" and loop.get("project") == name:
            pid = panel.loop_pid(home, inst["engine"])
            if pid:
                return pid
    return None


def _caught_signals(pid):
    """The mask of signals the process has a handler for (`SigCgt` in /proc/<pid>/status); None if unknown."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("SigCgt:"):
                return int(line.split()[1], 16)
    except (OSError, ValueError, IndexError):
        pass
    return None


def _handles_sigusr1(pid):
    """Whether SIGUSR1 reaches a handler. Without one the signal's default action would end the loop."""
    mask = _caught_signals(pid)
    return mask is not None and bool(mask & (1 << (signal.SIGUSR1 - 1)))


def interrupt(home, name):
    pid = _running_pid(home, name)
    if not pid:
        raise ApiError(409, "not_running", "no unit is running on this project")
    if not _handles_sigusr1(pid):
        # A loop started before this control existed (or one whose handlers cannot be read).
        raise ApiError(409, "restart_needed",
                       "this loop cannot take the interrupt request; stop and start it again first")
    _set_project(home, name, paused=True)  # before the signal: the loop must not pick the project again
    pid = _running_pid(home, name)  # both checks are repeated right before the signal
    if pid and _handles_sigusr1(pid):
        try:
            os.kill(pid, signal.SIGUSR1)
        except ProcessLookupError:
            pass
    return 202, {"ok": True}


def _lock_decisions():
    deadline = time.monotonic() + LOCK_WAIT
    while True:
        handle = coord.try_lock("decisions")
        if handle or time.monotonic() >= deadline:
            return handle
        time.sleep(0.05)


def answer_decision(home, project, decision_id, body):
    path = panel.project_path(home, project)
    if path is None:
        raise ApiError(404, "not_found", f"unknown project: {project}")
    option, comment = body.get("option"), body.get("comment")
    if not isinstance(option, str) or not option:
        raise bad("option is required")
    if comment is not None and (not isinstance(comment, str) or len(comment) > 2000):
        raise bad("comment must be a string of up to 2000 characters")
    handle = _lock_decisions()
    if handle is None:
        raise ApiError(409, "busy", "decisions are being processed; try again")
    try:
        found = [(f, d) for f, d in autopilot.open_decisions({"name": project, "path": path}) if d.get("id") == decision_id]
        if not found:
            raise ApiError(404, "not_found", "no open decision with this id")
        f, d = found[0]
        if option not in {o["id"] for o in d["options"]}:
            raise ApiError(400, "bad_option", "option is not listed in the decision")
        autopilot.answer_decision({"name": project, "path": path}, f, d, option, "owner")
        if comment and comment.strip():
            coord.add_note(project, f"Comment on the answer to decision {decision_id} (option {option}): {comment.strip()}")
    finally:
        coord.unlock(handle)
    overrides.update(home, lambda d: None)  # poke: wakes a sleeping loop
    return 200, {"ok": True}


def send_message(home, body):
    text, names = body.get("text"), body.get("projects")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
        raise bad("text must have 1 to 2000 characters")
    known = _known(home)[0]
    if names is None:
        names = known
    elif not (isinstance(names, list) and names and all(isinstance(n, str) and n in known for n in names)):
        raise bad("projects must be a non-empty list of known project names")
    for name in dict.fromkeys(names):
        coord.add_note(name, text.strip())
    overrides.update(home, lambda d: None)
    return 200, {"ok": True}


def _caps(value, names, kinds, what):
    """Checks {project: {engine: n}} (names given) or {engine: n} (names None) against the known ones."""
    if not isinstance(value, dict):
        raise bad(f"{what} must be an object")
    for key, v in value.items():
        if names is not None:
            if key not in names or not isinstance(v, dict) or not v:
                raise bad(f"{what}: unknown project or invalid value: {key!r}")
            pairs = v.items()
        else:
            pairs = [(key, v)]
        if not all(e in kinds and _number(n) for e, n in pairs):
            raise bad(f"{what}: unknown engine or cap outside 0 to 100 for {key!r}")


def _poke(home):
    overrides.update(home, lambda d: None)  # wakes a sleeping loop so it rereads the config


def _save(home, fn):
    """`config.save` with the problems of an invalid result turned into a 400; pokes the loops on success."""
    try:
        data = config.save(home, fn)
    except config.ConfigError as e:
        raise bad("; ".join(e.problems)) from e
    _poke(home)
    return data


def _check_fields(body, allowed):
    unknown = set(body) - set(allowed)
    if unknown:
        raise bad(f"unknown fields: {sorted(unknown)}")


def _folder(value):
    """The project folder as an absolute path string; it must exist and be a directory."""
    if not isinstance(value, str) or not value.strip():
        raise bad("path must be a non-empty string")
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise bad("path must be absolute")
    if not path.is_dir():
        raise ApiError(400, "bad_path", f"path is not an existing directory: {path}")
    return str(path)


def _group(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise bad("docker_group must be a string, null or empty")
    return value.strip()


def _project_caps(value):
    if not isinstance(value, dict):
        raise bad("weekly_cap must be an object engine -> number")
    for eng, n in value.items():
        if eng not in config.ENGINES or not _number(n):
            raise bad("weekly_cap: unknown engine or cap outside 0 to 100")
    return value


def add_project(home, body):
    _check_fields(body, ("name", "path", "docker_group", "weekly_cap"))
    name = body.get("name")
    if not isinstance(name, str) or not name:
        raise bad("name is required")
    path = _folder(body.get("path"))
    group = _group(body.get("docker_group"))
    caps = _project_caps(body["weekly_cap"]) if "weekly_cap" in body else None

    def fn(d):
        if any(isinstance(p, dict) and p.get("name") == name for p in d["projects"]):
            raise ApiError(409, "exists", f"a project named {name!r} already exists")
        item = {"name": name, "path": path}
        if group:
            item["docker_group"] = group
        if caps:
            item["weekly_cap"] = dict(caps)
        d["projects"].append(item)
    _save(home, fn)
    return 200, {"ok": True}


def edit_project(home, name, body):
    _check_fields(body, ("path", "docker_group", "weekly_cap"))
    path = _folder(body["path"]) if "path" in body else None
    caps = _project_caps(body["weekly_cap"]) if "weekly_cap" in body else None
    group = _group(body["docker_group"]) if "docker_group" in body else None

    def fn(d):
        p = next((p for p in d["projects"] if p.get("name") == name), None)
        if p is None:
            raise ApiError(404, "not_found", f"unknown project: {name}")
        if path is not None:
            p["path"] = path
        if "docker_group" in body:
            if group:
                p["docker_group"] = group
            else:
                p.pop("docker_group", None)
        if caps is not None:
            p["weekly_cap"] = {**(p.get("weekly_cap") or {}), **caps}
    _save(home, fn)
    return 200, {"ok": True}


def remove_project(home, name):
    """Drops the project from the config; state, history, claims and the folder are left alone."""
    def fn(d):
        if not any(p.get("name") == name for p in d["projects"]):
            raise ApiError(404, "not_found", f"unknown project: {name}")
        why = panel.blocked_by(home, name)
        if why:
            raise ApiError(409, "in_use", f"the project is in use ({why})")
        d["projects"] = [p for p in d["projects"] if p.get("name") != name]
    _save(home, fn)
    return 200, {"ok": True}


ENGINE_FIELDS = ("enabled", "orchestrator_model", "orchestrator_effort", "max_subagents", "primary_cap", "fable_cap",
                 "safety_factor", "blocked_cooldown_hours", "schedule", "roles")
ROLE_FIELDS = ("model", "effort")
SCHEDULE_FIELDS = ("start", "end", "start_margin_minutes")
DEFAULT_MARGIN = 15  # like schedule.from_config and the TOML import


def put_engine(home, engine, body):
    if engine not in config.ENGINES:
        raise ApiError(404, "not_found", f"unknown engine: {engine}")
    _check_fields(body, ENGINE_FIELDS)
    for key in body:
        if key not in config.ENGINE_DEFAULTS[engine]:
            raise bad(f"{key} does not apply to {engine}")
    new = dict(body)
    sched = new.get("schedule")
    if sched is not None:
        if not isinstance(sched, dict):
            raise bad("schedule must be null or an object")
        _check_fields(sched, SCHEDULE_FIELDS)
        new["schedule"] = {"start_margin_minutes": DEFAULT_MARGIN, **sched}
    roles = new.pop("roles", None)
    if roles is not None:
        if not isinstance(roles, dict):
            raise bad("roles must be an object")
        for role, v in roles.items():
            if role in config.ROLES and isinstance(v, dict):
                _check_fields(v, ROLE_FIELDS)

    def fn(d):
        e = d["engines"].setdefault(engine, copy.deepcopy(config.ENGINE_DEFAULTS[engine]))
        e.update(new)
        if roles is not None:
            # A partial body merges into the current roles (missing ones come from the defaults).
            merged = config.full_roles(engine, e.get("roles"))
            for role, v in roles.items():
                if role in config.ROLES and isinstance(v, dict):
                    merged[role].update(v)
                else:
                    merged[role] = v  # invalid: validation rejects it with the right message
            e["roles"] = merged
    _save(home, fn)
    return 200, {"ok": True}


_log_lock = threading.Lock()


def check_engine(home, engine):
    """Reads the engine's quota now. Writes no file: the state is a throwaway dict and the log is captured."""
    if engine not in config.ENGINES:
        raise ApiError(404, "not_found", f"unknown engine: {engine}")
    try:
        cfg = config.for_engine(config.load(home), engine)
    except (config.ConfigError, OSError) as e:
        return 200, {"limits": None, "error": str(e)}
    if not engines.cli_status(engine)["found"]:
        return 200, {"limits": None, "error": "CLI not found"}
    messages = []
    with _log_lock:  # engines.log writes autopilot.log; here it only collects
        previous, engines.log = engines.log, messages.append
        try:
            limits = engines.ENGINES[engine](cfg).limits({}, max_age=0)
        except (Exception, SystemExit) as e:  # the engines exit the process when their CLI is missing
            return 200, {"limits": None, "error": str(e) if isinstance(e, SystemExit) else f"{type(e).__name__}: {e}"}
        finally:
            engines.log = previous
    if not limits:
        return 200, {"limits": None, "error": messages[-1] if messages else "no quota reading"}
    return 200, {"limits": limits}


def put_settings(home, body):
    names, kinds = _known(home)
    _check_fields(body, ("order", "weekly_cap", "primary_cap"))
    order = body.get("order")
    if "order" in body and not (isinstance(order, list) and all(isinstance(n, str) and n in names for n in order)
                                and len(set(order)) == len(order)):
        raise bad("order must be a list of distinct known project names")
    if "weekly_cap" in body:
        _caps(body["weekly_cap"], names, kinds, "weekly_cap")
    if "primary_cap" in body:
        _caps(body["primary_cap"], None, kinds, "primary_cap")

    def fn(d):
        if "order" in body:
            rank = {n: i for i, n in enumerate(order)}
            # Listed projects first, in the given order; the rest keep their relative order after them.
            d["projects"].sort(key=lambda p: rank.get(p.get("name"), len(rank)))
        for name, per in body.get("weekly_cap", {}).items():
            p = next((p for p in d["projects"] if p.get("name") == name), None)
            if p is not None:
                p["weekly_cap"] = {**(p.get("weekly_cap") or {}), **per}
        for eng, n in body.get("primary_cap", {}).items():
            d["engines"][eng]["primary_cap"] = n
    _save(home, fn)
    return 200, panel.settings(home)


def write_route(home, method, parts, body):
    """Dispatches a write request to its function: (status, body)."""
    n = len(parts)
    if method == "POST" and n == 4 and parts[:2] == ["api", "engines"] and parts[3] in ("start", "stop"):
        return engine_action(home, parts[2], parts[3])
    if method == "POST" and n == 4 and parts[:2] == ["api", "engines"] and parts[3] == "check":
        return check_engine(home, parts[2])
    if method == "PUT" and n == 3 and parts[:2] == ["api", "engines"]:
        return put_engine(home, parts[2], body)
    if method == "POST" and parts == ["api", "projects"]:
        return add_project(home, body)
    if method == "PUT" and n == 3 and parts[:2] == ["api", "projects"]:
        return edit_project(home, parts[2], body)
    if method == "DELETE" and n == 3 and parts[:2] == ["api", "projects"]:
        return remove_project(home, parts[2])
    if method == "POST" and n == 4 and parts[:2] == ["api", "projects"]:
        return project_action(home, parts[2], parts[3])
    if method == "POST" and n == 4 and parts[:2] == ["api", "decisions"]:
        return answer_decision(home, parts[2], parts[3], body)
    if method == "POST" and parts == ["api", "messages"]:
        return send_message(home, body)
    if parts == ["api", "settings"] and method == "PUT":
        return put_settings(home, body)
    raise ApiError(404, "not_found")
