"""Loads the autopilot with every side effect (state, log, shared, ntfy) in a temporary folder."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "turno"  # the package: code, prompts, roles and the panel
sys.path.insert(0, str(ROOT))

MODULES = ("autopilot", "coord", "engines", "schedule", "paths", "history", "backlog", "feed", "panel", "control", "server",
           "messages")


def load(tmp):
    os.environ["TURNO_HOME"] = str(tmp)
    # A test never reaches the real ~/.codex (the Codex engine writes its role files there).
    os.environ["CODEX_HOME"] = str(Path(tmp) / "codex-home")
    pkg = sys.modules.get("turno")
    for name in MODULES:
        sys.modules.pop(f"turno.{name}", None)
        if pkg is not None and name in vars(pkg):
            delattr(pkg, name)  # or `from turno import x` would hand back the old module
    from turno import autopilot as ap
    ap.STATE, ap.LOG, ap.RUNS = tmp / "state.json", tmp / "log", tmp / "runs"
    ap.HISTORY = tmp / "history-codex.jsonl"
    ap.SECRETS = tmp / "secrets.toml"
    if "turno.coord" in sys.modules:
        sys.modules["turno.coord"].SHARED = tmp / "shared"
    from turno import pypi
    pypi._fetch = _no_network  # a test never reaches PyPI; the tests of the check patch it again
    ap.sent = []
    ap.real_ntfy = ap.ntfy  # for the tests of the sender itself, with the network mocked
    ap.ntfy = lambda cfg, title, message, **kw: ap.sent.append((title, message))
    return ap


def _no_network(url):
    raise AssertionError(f"a test tried to reach {url}")


def git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], capture_output=True, check=True)
    run("init", "-q")
    (path / "README").write_text("x\n")
    run("add", ".")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return path


def isolate_git(test, tmp):
    """Keeps the user's global git config and ignore file out of `test`: either could ignore `.superpowers/`."""
    from unittest import mock
    xdg = Path(tmp) / "xdg-config"
    xdg.mkdir(parents=True, exist_ok=True)
    patch = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "XDG_CONFIG_HOME": str(xdg)})
    patch.start()
    test.addCleanup(patch.stop)


class FakeEngine:
    """Test engine: runs `cmd` and never reads a real quota."""

    label = "Fake"
    needs_limits = False

    def __init__(self, cmd, name="claude", limits=None):
        self.cmd, self.name, self._limits = cmd, name, limits if limits is not None else {}
        self.limits_calls = 0
        self.max_ages = []

    def describe(self):
        return "fake"

    def limits(self, state, max_age=1800):
        self.limits_calls += 1
        self.max_ages.append(max_age)
        return self._limits

    def resumable(self, s):
        return bool(s["session"])

    def begin(self, s, resume):
        if not resume:
            s["session"] = "fake-session"

    def command(self, state, project, s, resume, stem):
        return list(self.cmd), None

    def after_run(self, state, s, out, resume):
        pass

    def classify(self, out, err, code):
        return ("ok" if code == 0 else "error"), None, False

    def unit_cost(self, state, s):
        return None

    def close_unit(self, s):
        pass


DEFAULTS = '[defaults]\norchestrator_model = "m"\norchestrator_effort = "medium"\nprimary_cap = 100\n'


def write_config(home, projects=(), enabled=("codex", "claude"), edit=None):
    """Writes a valid `config.json`: `projects` are its entries, `enabled` the engines switched on.

    `edit(data)` may change anything else before the file is written. The file's mtime always moves
    forward, even for two writes within the same clock tick.
    """
    from turno import config
    data = config.empty()
    data["projects"] = [dict(p) for p in projects]
    for name in enabled:
        data["engines"][name]["enabled"] = True
    if edit:
        edit(data)
    write_config_text(home, json.dumps(data, indent=2))
    return data


def write_config_text(home, text):
    """Puts `text` in `config.json` as it is (valid or not), moving the mtime forward."""
    from turno import config
    path = Path(home) / config.FILE
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(text)
    if path.stat().st_mtime_ns <= before:
        os.utime(path, ns=(before + 1_000_000, before + 1_000_000))


def make_home(home, topic="segredo-xyz"):
    """Sample data folder: Codex and Claude, three projects, `config.json` and the ntfy topic in `secrets.toml`."""
    home = Path(home)
    projects = {}
    for name in ("my-app", "my-api", "side-project"):
        projects[name] = home / "projects" / name
        (projects[name] / ".superpowers" / "autopilot" / "decisions").mkdir(parents=True)
    (home / "secrets.toml").write_text(f'[notify]\ntopic = "{topic}"\n')

    def models(data):
        for e in data["engines"].values():
            e["orchestrator_model"] = "m"

    write_config(home, [{"name": n, "path": str(p), "weekly_cap": {"codex": 90, "claude": 90}}
                        for n, p in projects.items()], edit=models)
    return projects
