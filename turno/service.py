"""The background service that keeps the panel up and, through it, the loops.

Linux and WSL with systemd: a user unit, `~/.config/systemd/user/turno.service`. macOS: a launchd
agent, `~/Library/LaunchAgents/dev.turno.panel.plist`. Both run `<python> -m turno serve
--start-engines`. Only the panel process belongs to the service: the loops live in tmux, so stopping
or restarting the service never stops them (`KillMode=process`; `AbandonProcessGroup`).

Never runs `sudo`, never overwrites a different existing file without asking, and every outside
command goes through `_run`, which the tests replace.
"""

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

UNIT = "turno.service"
LABEL = "dev.turno.panel"


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)


def kind():
    """"systemd", "launchd" or None (neither available: run `turno serve` by hand)."""
    if sys.platform == "darwin":
        return "launchd" if shutil.which("launchctl") else None
    if shutil.which("systemctl") and Path("/run/systemd/system").is_dir():
        return "systemd"
    return None


def unit_path():
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "systemd" / "user" / UNIT


def plist_path():
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def path_for(k):
    return unit_path() if k == "systemd" else plist_path()


def environment(data_home):
    """The service's environment: the PATH of whoever installs it (so tmux, git, docker and the CLIs
    are found, as in their shell) and TURNO_HOME when it is not the default `~/.turno`."""
    env = {"PATH": os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin"}
    if Path(data_home).expanduser().resolve() != (Path.home() / ".turno").resolve():
        env["TURNO_HOME"] = str(Path(data_home).expanduser().resolve())
    return env


def _sd_quote(value):
    """One systemd unit value, quoted: `%` is a specifier and `$` a variable there."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


def systemd_unit(argv, env):
    lines = [
        "[Unit]",
        "Description=turno panel (starts the enabled engines)",
        "",
        "[Service]",
        "ExecStart=" + " ".join(_sd_quote(a) for a in [*argv, "serve", "--start-engines"]),
        *(f"Environment={_sd_quote(f'{k}={v}')}" for k, v in env.items()),
        "Restart=on-failure",
        "RestartSec=10",
        "# Only the panel: the loops it starts live in tmux and outlive a stop or restart of the service.",
        "KillMode=process",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(lines)


def launchd_plist(argv, env, data_home):
    log = str(Path(data_home).expanduser().resolve() / "panel.log")
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [*argv, "serve", "--start-engines"],
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "AbandonProcessGroup": True,  # the loops (tmux) and a restart helper survive the agent
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }).decode()


def content(k, argv, data_home):
    env = environment(data_home)
    return systemd_unit(argv, env) if k == "systemd" else launchd_plist(argv, env, data_home)


def _domain():
    return f"gui/{os.getuid()}"


def manual_hint():
    return ("no systemd user session or launchd here: keep the panel running yourself, e.g. "
            "`tmux new -d -s turno-panel turno serve --start-engines`")


def install(data_home, argv, ask=input, out=print):
    """Writes the unit or agent and enables it. Returns 0 when the service is installed and started."""
    k = kind()
    if k is None:
        out(manual_hint())
        return 1
    path, text = path_for(k), content(k, argv, data_home)
    if path.exists() and path.read_text() != text:
        answer = ask(f"{path} exists and is different. Replace it? [y/N] ").strip().lower()
        if answer not in ("y", "yes", "s", "sim"):
            out(f"kept {path}; the service was not changed")
            return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if k == "systemd":
        steps = [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", UNIT]]
    else:
        _run(["launchctl", "bootout", f"{_domain()}/{LABEL}"])  # an older copy; fails harmlessly when absent
        steps = [["launchctl", "bootstrap", _domain(), str(path)]]
    for argv_step in steps:
        r = _run(argv_step)
        if r.returncode != 0:
            out(f"`{shlex.join(argv_step)}` failed: {(r.stderr or r.stdout).strip()}")
            return 1
    out(f"service installed: {path}")
    if k == "systemd":
        out("to start it at boot without logging in, run once: loginctl enable-linger $USER")
    return 0


def uninstall(out=print):
    """Stops and removes the service. The loops keep running (they are not part of it)."""
    k = kind()
    if k is None:
        out(manual_hint())
        return 1
    path = path_for(k)
    if not path.exists():
        out("the service is not installed")
        return 0
    if k == "systemd":
        _run(["systemctl", "--user", "disable", "--now", UNIT])
        path.unlink()
        _run(["systemctl", "--user", "daemon-reload"])
    else:
        _run(["launchctl", "bootout", f"{_domain()}/{LABEL}"])
        path.unlink()
    out(f"service removed ({path}); the loops were left running")
    return 0


def status():
    """{"kind", "installed", "active"}: whether the file exists and the service manager runs it."""
    k = kind()
    if k is None:
        return {"kind": None, "installed": False, "active": False}
    installed = path_for(k).exists()
    if not installed:
        return {"kind": k, "installed": False, "active": False}
    if k == "systemd":
        active = _run(["systemctl", "--user", "is-active", "--quiet", UNIT]).returncode == 0
    else:
        active = _run(["launchctl", "print", f"{_domain()}/{LABEL}"]).returncode == 0
    return {"kind": k, "installed": True, "active": active}


def restart():
    """Restarts the installed service; True when the service manager accepted it."""
    k = kind()
    if k is None or not path_for(k).exists():
        return False
    if k == "systemd":
        argv = ["systemctl", "--user", "restart", UNIT]
    else:
        argv = ["launchctl", "kickstart", "-k", f"{_domain()}/{LABEL}"]
    return _run(argv).returncode == 0
