"""Carrega o autopilot com todo efeito colateral (estado, log, shared, ntfy) numa pasta temporária."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODULES = ("autopilot", "coord", "engines", "schedule", "paths", "history", "backlog", "feed", "panel", "server")


def load(tmp):
    os.environ["TURNO_HOME"] = str(tmp)
    for name in MODULES:
        sys.modules.pop(name, None)
    import autopilot as ap
    ap.STATE, ap.LOG, ap.RUNS = tmp / "state.json", tmp / "log", tmp / "runs"
    ap.HISTORY = tmp / "history-codex.jsonl"
    ap.SECRETS = tmp / "secrets.toml"
    if "coord" in sys.modules:
        sys.modules["coord"].SHARED = tmp / "shared"
    ap.sent = []
    ap.ntfy = lambda cfg, title, message, **kw: ap.sent.append((title, message))
    return ap


def git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], capture_output=True, check=True)
    run("init", "-q")
    (path / "README").write_text("x\n")
    run("add", ".")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    return path


class FakeEngine:
    """Motor de teste: roda `cmd` e não lê cota de verdade."""

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


def make_home(home, topic="segredo-xyz"):
    """Pasta de dados de exemplo: Codex e Claude, três projetos, e o tópico do ntfy nos dois lugares."""
    home = Path(home)
    projects = {}
    for name in ("my-app", "my-api", "side-project"):
        projects[name] = home / "projects" / name
        (projects[name] / ".superpowers" / "autopilot" / "decisions").mkdir(parents=True)
    body = "".join(f'\n[[project]]\nname = "{n}"\npath = "{p}"\nweekly_cap = 90\n' for n, p in projects.items())
    (home / "projects.toml").write_text(DEFAULTS + f'\n[notify]\ntopic = "{topic}"\n' + body)
    (home / "claude.toml").write_text('[instance]\nname = "claude"\n\n[engine]\nkind = "claude"\n\n' + DEFAULTS + body)
    (home / "secrets.toml").write_text(f'[notify]\ntopic = "{topic}"\n')
    return projects
