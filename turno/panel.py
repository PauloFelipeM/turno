"""What the panel shows, assembled from turno's files.

Read-only (the writes live in control.py): it writes nothing and takes no locks. Of secrets.toml it only learns
whether a topic exists (`paths.has_ntfy_topic`): the topic itself never reaches it. It reads the setup requests and
the drafts, never through a symlink.
"""

import datetime
import json
import os
import re
import subprocess
import time
from pathlib import Path

from . import autopilot
from . import backlog
from . import config
from . import engines
from . import feed
from . import history
from . import overrides
from . import paths
from . import schedule
from . import repo_setup as setup


def read_json(path):
    """Reads a JSON file and returns only objects (dict); a missing, invalid or other-typed file is None."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _dict(x):
    return x if isinstance(x, dict) else {}


def _future(ts, now):
    """`ts` if it is a time still ahead of `now`; None otherwise (0, past or not a number)."""
    return ts if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > now else None


def instances(home):
    """One instance per engine in `config.json` (disabled ones too: the overview shows them as off).

    An unreadable or invalid config gives a single error entry."""
    home = Path(home)
    try:
        data = config.load(home)
    except (config.ConfigError, OSError):
        return [{"config": config.FILE, "error": "config"}]
    out = []
    for kind in config.ENGINES:
        if kind not in data["engines"]:
            continue
        cfg = config.for_engine(data, kind)
        suffix = "-claude" if kind == "claude" else ""
        out.append({"config": config.FILE, "cfg": cfg, "engine": kind, "enabled": cfg["enabled"],
                    "state": home / f"state{suffix}.json", "runs": home / f"runs{suffix}",
                    "history": home / f"history-{kind}.jsonl"})
    return out


def configured(home):
    return [i for i in instances(home) if "error" not in i]


PROC = Path("/proc")  # tests point it at a missing folder to play macOS


def _is_autopilot_command(command):
    """Whether a `ps` command line (one string) runs autopilot.py; a path with spaces still counts."""
    return re.search(r"autopilot\.py(\s|$)", command) is not None


def loop_pid(home, kind):
    """The loop's pid if it is alive. Reads the pid from the lock file without touching the lock (no flock)."""
    try:
        pid = int((Path(home) / "shared" / f"instance-{kind}.lock").read_text().strip())
    except (OSError, ValueError):
        return None
    if pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    try:
        args = (PROC / str(pid) / "cmdline").read_bytes().split(b"\0")
    except FileNotFoundError:
        # No /proc (macOS): ask `ps` for the command line, as the Linux check does with /proc, because
        # a live pid may belong to another process that took the number after the loop died.
        try:
            command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True,
                                     text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        return pid if _is_autopilot_command(command) else None
    except OSError:
        return None
    # The pid may have been reused by another process after the loop died.
    return pid if any(a.endswith(b"autopilot.py") for a in args) else None


def _engine(inst, homes):
    kwargs = {"home": homes[inst["engine"]]} if homes and inst["engine"] in homes else {}
    return engines.ENGINES[inst["engine"]](inst["cfg"], **kwargs)


def _engine_view(home, inst, state, homes):
    cfg = inst["cfg"]
    eng = _engine(inst, homes)
    try:
        limits = eng.limits(state, max_age=float("inf")) or {}  # never queries the quota: only the last reading
    except Exception:
        limits = {}
    pid = loop_pid(home, inst["engine"])
    loop = dict(_dict(state.get("_loop")) or {"phase": "unknown"}) if pid else {"phase": "stopped"}
    window = None
    win = schedule.from_config(cfg)
    if win:
        now = datetime.datetime.now()
        inside = win.contains(now)
        window = {"label": win.describe(cfg.get("language")), "open": inside,
                  "until": (win.end_of(now) if inside else win.next_start(now)).timestamp()}

    def usage(key):
        used, reset = engines.window(limits, key)
        return {"used": used, "resets_at": reset or None}

    return {"engine": inst["engine"], "label": eng.label, "config": inst["config"], "enabled": inst["enabled"], "pid": pid, "loop": loop,
            "limits": {"known": bool(limits.get("primary") or limits.get("secondary")),
                       "primary": usage("primary"), "secondary": usage("secondary")},
            "primary_cap": _dict(cfg.get("defaults")).get("primary_cap"),
            "weekly_cap": max([v for p in cfg["project"] for v in [p.get("weekly_cap")]
                              if isinstance(v, (int, float)) and not isinstance(v, bool)], default=0),
            "window": window}


_DECISION_KEYS = ("id", "question", "context", "options", "default", "deadline", "notified_at")


def pending_decisions(project_path):
    out = []
    for path in autopilot.decision_files({"path": str(project_path)}):
        d = read_json(path)
        if d and "answer" not in d and autopilot.valid_decision(d, path):
            out.append({k: d.get(k) for k in _DECISION_KEYS})
    return out


def answerable_decisions(project_path):
    """The decisions that accept an answer, like `autopilot.open_decisions`: sent, unanswered and valid.

    Built from `pending_decisions` because `open_decisions` trusts the files to be objects."""
    return [d for d in pending_decisions(project_path) if d.get("notified_at")]


def _backlog_summary(project_path, cache):
    if not Path(project_path).is_dir():
        return {"error": "missing_path"}
    b = backlog.read_backlog(project_path, cache)
    if "error" in b:
        return b
    items = b["items"]
    out = {"current": backlog.current(items), "done": sum(1 for i in items if i["state"] == "done"), "total": len(items)}
    if b.get("stale"):
        out["stale"] = True
    return out


def live_runs(home, homes=None):
    """The runs in progress, with the JSONL path (for the server's internal use)."""
    out = []
    for inst in configured(home):
        state = read_json(inst["state"]) or {}
        phase = _dict(state.get("_loop"))
        if phase.get("phase") != "running" or not loop_pid(home, inst["engine"]):
            continue
        name = phase.get("project")
        s = _dict(state.get(name))
        run = _dict(s.get("run"))
        if not run.get("id"):
            continue
        root = next((p["path"] for p in inst["cfg"]["project"] if p["name"] == name), None)
        out.append({"engine": inst["engine"], "project": name, "unit_id": s.get("unit_id"), "run": run["id"],
                    "started_at": history.iso(run.get("started") or time.time()),
                    "path": str(inst["runs"] / f"{run['id']}.jsonl"), "root": root})
    return out


def live(home, homes=None):
    """The runs in progress with their feed so far, for whoever opens the panel in the middle of them."""
    out = []
    for r in live_runs(home, homes):
        out.append({"run": r["run"], "unit_id": r["unit_id"], "project": r["project"], "engine": r["engine"],
                    "started_at": r["started_at"], "items": feed.read_run(r["engine"], r["path"], r["root"]) or []})
    return out


def general(home):
    """The owner's language and whether ntfy is on and has a topic, for the page; defaults when the config is unreadable."""
    try:
        data = config.load(home)
    except (config.ConfigError, OSError):
        data = {}
    return {"language": data.get("language", config.DEFAULT_LANGUAGE),
            "notify": {"enabled": config.notify_enabled(data.get("notify")), "has_topic": paths.has_ntfy_topic(home)}}


def overview(home, cache, homes=None):
    home = Path(home)
    now = time.time()
    views, errors, projects = [], [], {}
    for inst in instances(home):
        if "error" in inst:
            errors.append({"config": inst["config"], "error": inst["error"]})
            continue
        try:
            state = read_json(inst["state"]) or {}
            view = _engine_view(home, inst, state, homes)
            mine = {}
            for p in inst["cfg"]["project"]:
                s = _dict(state.get(p["name"]))
                mine[p["name"]] = (p, {
                    "status": s.get("status", "active"), "interrupted": bool(s.get("interrupted")),
                    "blocked_until": s.get("blocked_until") or None, "weekly_cap": p.get("weekly_cap"), "cost": s.get("cost"),
                    "retry_at": _future(s.get("retry_at"), now)})
        except Exception:
            errors.append({"config": inst["config"], "error": "engine"})
            continue
        views.append(view)
        for name, (p, info) in mine.items():
            entry = projects.setdefault(name, {"name": name, "path": p["path"], "engines": {}})
            entry["engines"][inst["engine"]] = info
    ov = overrides.read(home)
    ov_projects = _dict(ov.get("projects"))
    blocked = {}
    if any(e["status"] == "blocked" for entry in projects.values() for e in entry["engines"].values()):
        # The history of every engine, read once and only when some project is blocked.
        for u in units(home, limit=10**9):  # newest first
            if u["closed"] and u["outcome"] == "blocked":
                blocked.setdefault(u["project"], u["summary"])
    out_projects = []
    for entry in projects.values():
        claim = read_json(home / "shared" / "claims" / f"{entry['name']}.json")
        is_dir = Path(entry["path"]).is_dir()
        count = len(pending_decisions(entry["path"])) if is_dir else 0
        open_list = answerable_decisions(entry["path"]) if is_dir else []
        out_projects.append({
            "name": entry["name"], "engines": entry["engines"],
            "claim": {"engine": claim.get("engine"), "handoff_from": claim.get("handoff_from")} if claim else None,
            "backlog": _backlog_summary(entry["path"], cache),
            "decisions": count, "decisions_open": open_list,
            "paused": _dict(ov_projects.get(entry["name"])).get("paused") is True,
            "next": ov.get("next") == entry["name"],
            "setup": setup.phase(home, entry["name"]),
            "blocked_summary": blocked.get(entry["name"]) if any(
                e["status"] == "blocked" for e in entry["engines"].values()) else None})
    return {"engines": views, "projects": out_projects, "errors": errors, "now": now, **general(home),
            "live": [{k: v for k, v in r.items() if k not in ("path", "root")} for r in live_runs(home, homes)]}


def settings(home):
    """The order and caps in effect (read from `config.json`)."""
    order, engines_view = [], {}
    for inst in configured(home):
        cfg = inst["cfg"]
        order += [p["name"] for p in cfg["project"] if p["name"] not in order]
        engines_view[inst["engine"]] = {
            "primary_cap": _dict(cfg.get("defaults")).get("primary_cap"),
            "weekly_cap": {p["name"]: p.get("weekly_cap") for p in cfg["project"]}}
    return {"order": order, "engines": engines_view}


def project_checks(path):
    """Warning codes for a project folder (see the spec); nothing here blocks anything."""
    root = Path(path)
    if not root.is_dir():
        return ["missing_path"]
    out = []
    if not (root / ".git").exists():
        out.append("not_git")
    if not (root / "docs" / "autopilot.md").is_file():
        out.append("no_autopilot_doc")
    if "not_git" not in out:
        try:
            r = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", ".superpowers/autopilot/last-unit.json"],
                               capture_output=True, stdin=subprocess.DEVNULL, timeout=5)
            ignored = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            ignored = True  # git unavailable: no basis for a warning
        if not ignored:
            out.append("superpowers_not_ignored")
    return out


def blocked_by(home, name):
    """Why a project cannot be removed: "running", "interrupted", "claimed"; None when it can."""
    home = Path(home)
    states = []
    for inst in instances(home):
        if "error" in inst:
            continue
        state = read_json(inst["state"]) or {}
        states.append(state)
        loop = _dict(state.get("_loop"))
        if loop.get("phase") == "running" and loop.get("project") == name and loop_pid(home, inst["engine"]):
            return "running"
    if any(_dict(st.get(name)).get("interrupted") for st in states):
        return "interrupted"
    if (home / "shared" / "claims" / f"{name}.json").exists():
        return "claimed"
    return None


def config_view(home):
    """What the settings screen edits: projects with their warnings, engines with their CLI status, notify.

    Raises `config.ConfigError` when `config.json` is invalid."""
    data = config.load(home)
    projects = []
    for p in data["projects"]:
        why = blocked_by(home, p["name"])
        projects.append({"name": p["name"], "path": p["path"], "docker_group": p.get("docker_group"),
                         "weekly_cap": dict(p.get("weekly_cap") or {}), "checks": project_checks(p["path"]),
                         "removable": why is None, "blocked_by": why, "setup": setup.phase(home, p["name"])})
    engine_view = {}
    for kind in config.ENGINES:
        if kind in data["engines"]:
            engine_view[kind] = {**config.ENGINE_DEFAULTS[kind], **data["engines"][kind],
                                 "cli": engines.cli_status(kind)}
            engine_view[kind]["roles"] = config.full_roles(kind, data["engines"][kind].get("roles"))
            engine_view[kind]["known_models"] = list(config.KNOWN_MODELS[kind])
    notify = data["notify"]
    enabled, has_topic = config.notify_enabled(notify), paths.has_ntfy_topic(home)
    # Only these keys: a topic written into config.json by hand is never sent to the page.
    return {"projects": projects, "engines": engine_view,
            "notify": {"enabled": enabled, "server": notify.get("server"),
                       "decision_timeout_hours": notify.get("decision_timeout_hours"), "has_topic": has_topic},
            "language": data.get("language", config.DEFAULT_LANGUAGE), "languages": list(config.LANGUAGES),
            "warnings": ["notify_no_topic"] if enabled and not has_topic else []}


def project_path(home, name):
    for inst in configured(home):
        for p in inst["cfg"]["project"]:
            if p["name"] == name:
                return p["path"]
    return None


def setup_detail(home, name):
    """The project's setup request and its draft files; None for an unknown project."""
    path = project_path(home, name)
    if path is None:
        return None
    return {"request": setup.read(home, name), "files": setup.read_drafts(path) if Path(path).is_dir() else []}


def project_detail(home, name, cache):
    path = project_path(home, name)
    if path is None:
        return None
    if not Path(path).is_dir():
        return {"name": name, "backlog": {"error": "missing_path"}, "decisions": []}
    return {"name": name, "backlog": backlog.read_backlog(path, cache), "decisions": pending_decisions(path)}


def units(home, project=None, before=None, limit=50):
    entries = [e for inst in configured(home) for e in history.read(inst["history"])]
    out = history.group(entries)
    if project:
        out = [u for u in out if u["project"] == project]
    if before:
        cutoff = history.ts(before)
        out = [u for u in out if history.ts(u["started_at"]) < cutoff]
    return out[:limit]


def unit_detail(home, unit_id):
    insts = {i["engine"]: i for i in configured(home)}
    entries = [e for i in insts.values() for e in history.read(i["history"]) if e.get("unit_id") == unit_id]
    if not entries:
        return None
    unit = history.group(entries)[0]
    root = project_path(home, unit["project"])
    for r in unit["runs"]:
        inst = insts.get(r.get("engine"))
        r["feed"] = feed.read_run(r["engine"], inst["runs"] / f"{r['run']}.jsonl", root) if inst and r.get("run") else None
    return unit


def watched_files(home):
    """The files whose changes change the overview."""
    home = Path(home)
    files = [home / config.FILE]
    files += list(home.glob("state*.json")) + list(home.glob("history-*.jsonl")) + [home / overrides.FILE]
    files += list((home / setup.FOLDER).glob("*.json"))
    files += list((home / "shared" / "claims").glob("*.json")) + list((home / "shared").glob("instance-*.lock"))
    for inst in configured(home):
        for p in inst["cfg"]["project"]:
            base = Path(p["path"]) / ".superpowers" / "autopilot"
            files.append(base / "backlog.json")
            files += list((base / "decisions").glob("*.json"))
    return files

