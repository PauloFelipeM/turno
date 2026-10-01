"""Carrega o autopilot com todo efeito colateral (estado, log, shared, ntfy) numa pasta temporária."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODULES = ("autopilot", "coord", "engines", "schedule")


def load(tmp):
    for name in MODULES:
        sys.modules.pop(name, None)
    import autopilot as ap
    ap.STATE, ap.LOG, ap.RUNS = tmp / "state.json", tmp / "log", tmp / "runs"
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
