"""`turno restart` and `turno update`.

`restart` stops the loops like `stop` (their units resume), restarts the panel's service and lets
the panel start the engines again. `update` upgrades the package with the tool that installed it,
then restarts. Every outside command goes through `_run`, which the tests replace.
"""

import importlib.metadata
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import autopilot
from . import config
from . import service


def _run(argv, **kw):
    return subprocess.run(argv, **kw)


STUCK_WAIT = 60  # seconds to wait, after `stop_loops` gave up, for a loop to end its unit


def _sleep(seconds):
    time.sleep(seconds)


def running_engines():
    """The engines whose loop runs now (any form of command line)."""
    return [k for k in config.ENGINES if autopilot.running_pid(k) is not None or autopilot.loop_pids(k)]


def autostart_engines(home):
    try:
        data = config.load(home)
    except (config.ConfigError, OSError):
        return []
    return [k for k in config.ENGINES if isinstance(data["engines"].get(k), dict)
            and data["engines"][k].get("enabled") is True and data["engines"][k].get("autostart", True) is not False]


def restart(home, out=print):
    """Stop the loops, restart the service; the panel it runs starts the autostart engines.

    The loops that were running and that the panel will not start (autostart off) are started here.
    Without a service, or when it does not restart, every loop that was running is started here
    again and the panel is left to the owner. Returns 0 on success."""
    before = running_engines()
    stopped = autopilot.stop_loops() or {}
    for kind in [k for k, v in stopped.items() if v == "still stopping"]:
        deadline = time.monotonic() + STUCK_WAIT
        while time.monotonic() < deadline and (autopilot.running_pid(kind) is not None or autopilot.loop_pids(kind)):
            _sleep(1)
        if autopilot.running_pid(kind) is not None or autopilot.loop_pids(kind):
            msg = (f"{kind}: the loop is still finishing its unit; it was not started again. "
                   f"Use \"Iniciar\" in the panel once it ends")
            out(msg)
            autopilot.log(msg)
            before = [k for k in before if k != kind]
    st = service.status()
    if st["installed"] and service.restart():
        out("the panel's service restarted; it starts the engines with autostart on")
        rest = [k for k in before if k not in autostart_engines(home)]
        if rest:
            autopilot.start_loops(rest)
        return 0
    if st["installed"]:
        out("the service did not restart (see `turno service status`); starting the loops here")
    else:
        out("no service installed: the loops restart here; restart the panel yourself "
            "(stop `turno serve` and run it again), or install the service with `turno service install`")
    if before:
        autopilot.start_loops(before)
    return 0 if not st["installed"] else 1


def installer():
    """How this copy was installed: "pipx", "uv", "editable", "source" (a checkout used through its
    root shim, not installed) or "other" (pip or anything else)."""
    if not autopilot.runs_as_module():
        return "source"
    try:
        dist = importlib.metadata.distribution("turno")
    except importlib.metadata.PackageNotFoundError:
        return "source"
    try:
        direct = json.loads(dist.read_text("direct_url.json") or "{}")
    except ValueError:
        direct = {}
    if isinstance(direct, dict) and (direct.get("dir_info") or {}).get("editable"):
        return "editable"
    prefix = Path(sys.prefix).resolve()
    parts = prefix.parts
    pipx_home = os.environ.get("PIPX_HOME")
    if prefix.parent.name == "venvs" and ("pipx" in parts or (pipx_home and prefix.parent == Path(pipx_home).resolve() / "venvs")):
        return "pipx"
    uv_dir = os.environ.get("UV_TOOL_DIR")
    if ("uv" in parts and "tools" in parts) or (uv_dir and prefix.parent == Path(uv_dir).resolve()):
        return "uv"
    return "other"


UPGRADE = {"pipx": ["pipx", "upgrade", "turno"], "uv": ["uv", "tool", "upgrade", "turno"]}


def update(home, out=print):
    """Upgrades with pipx or uv, then runs the new code's `restart`. Returns an exit code."""
    how = installer()
    if how in ("editable", "source"):
        out(f"turno runs from a source checkout ({autopilot.BASE.parent}): run `git pull` there, rebuild the panel "
            f"(`npm --prefix ui ci && npm --prefix ui run build`), then `turno restart`")
        return 1
    if how == "other":
        out("turno was not installed with pipx or uv: upgrade it with the tool you used "
            "(for pip: `python3 -m pip install -U turno`), then run `turno restart`")
        return 1
    argv = UPGRADE[how]
    out(f"running: {' '.join(argv)}")
    try:
        r = _run(argv)
    except OSError as e:
        out(f"could not run {argv[0]}: {e}; nothing was restarted")
        return 1
    if r.returncode != 0:
        out("the upgrade failed; nothing was restarted")
        return 1
    # The new code restarts: this process still has the old modules loaded.
    env = {**os.environ, "TURNO_HOME": str(home)}
    return _run([*autopilot.turno_argv(), "restart"], env=env).returncode
