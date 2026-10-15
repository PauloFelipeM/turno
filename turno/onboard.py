"""`turno onboard`: checks the machine, asks a few questions, writes `~/.turno`, installs the service
and opens the panel.

Safe to run again: it never overwrites an existing file without asking, prints the ntfy topic only
when it creates it, and never runs `sudo`. Every question can be answered by a flag; `--yes` takes
the default of every question a flag does not answer. Outside commands go through `_run` and
`_which`, which the tests replace.
"""

import argparse
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from . import autopilot
from . import config
from . import engines
from . import paths
from . import service

PORT = 8765
YES = ("y", "yes", "s", "sim")
NO = ("n", "no", "nao", "não")


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20)


def _which(name):
    return shutil.which(name)


def add_arguments(parser):
    parser.add_argument("--language", choices=config.LANGUAGES, help="language of the panel and the phone texts")
    parser.add_argument("--engines", help="comma-separated engines to enable, e.g. codex,claude (only those found count)")
    parser.add_argument("--ntfy", action=argparse.BooleanOptionalAction, default=None, help="send decisions to your phone with ntfy")
    parser.add_argument("--service", action=argparse.BooleanOptionalAction, default=None, help="install the background service")
    parser.add_argument("--yes", action="store_true", help="take the default answer of every question not given by a flag")
    parser.add_argument("--no-open", action="store_true", help="do not open the browser at the end")


# --- checks --------------------------------------------------------------------------------

def _version(argv):
    try:
        r = _run(argv)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (r.stdout or r.stderr or "").strip().splitlines()
    return lines[0].strip() if r.returncode == 0 and lines else None


def _codex_login():
    """"logged in", "not logged in" or "could not tell" (`codex login status` reads no quota)."""
    try:
        r = _run(["codex", "login", "status"])
    except (OSError, subprocess.SubprocessError):
        return "could not tell"
    return "logged in" if r.returncode == 0 else "not logged in (run `codex login`)"


def checks():
    """[(name, ok, detail)] and the engines whose CLI was found. Only "no engine CLI" stops the wizard."""
    rows = [("Python", sys.version_info >= (3, 11), sys.version.split()[0])]
    for name, flag, fix in (("git", "--version", "install git"), ("tmux", "-V", "install tmux (it keeps the loops running)")):
        if _which(name):
            rows.append((name, True, _version([name, flag]) or "found"))
        else:
            rows.append((name, False, f"missing: {fix}"))
    docker = _which("docker")
    rows.append(("Docker (optional)", True, "found" if docker else "not found; only needed by projects that use it"))
    found_engines = []
    for kind in config.ENGINES:
        cli = engines.cli_status(kind, max_age=0)
        label = "Codex CLI" if kind == "codex" else "Claude Code"
        if not cli["found"]:
            rows.append((label, False, "not found: install it and log in to use this engine"))
            continue
        found_engines.append(kind)
        login = _codex_login() if kind == "codex" else "login: could not tell (run `claude` once if you never did)"
        rows.append((label, True, f"{cli['version'] or 'version unknown'}; {login}"))
    return rows, found_engines


# --- questions -----------------------------------------------------------------------------

class Asker:
    def __init__(self, yes, ask, out):
        self.yes, self.ask, self.out = yes, ask, out

    def _input(self, prompt, default):
        """The answer, or `default` (with a message) when there is no terminal to ask on."""
        try:
            return self.ask(prompt).strip()
        except EOFError:
            self.out(f"no terminal to answer on: taking the default for \"{prompt.strip()}\" ({default})")
            return ""

    def yes_no(self, question, default):
        if self.yes:
            return default
        hint = "[Y/n]" if default else "[y/N]"
        while True:
            answer = self._input(f"{question} {hint} ", "yes" if default else "no").lower()
            if not answer:
                return default
            if answer in YES:
                return True
            if answer in NO:
                return False
            self.out("answer y or n")

    def choice(self, question, options, default):
        if self.yes:
            return default
        while True:
            answer = self._input(f"{question} [{'/'.join(options)}] ({default}) ", default)
            if not answer:
                return default
            match = next((o for o in options if o.lower() == answer.lower()), None)
            if match:
                return match
            self.out(f"answer one of: {', '.join(options)}")


def ntfy_topic():
    return "turno-" + secrets.token_urlsafe(18)


def write_topic(home, topic):
    """Creates `secrets.toml` with mode 600; never replaces an existing file."""
    fd = os.open(Path(home) / "secrets.toml", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f'[notify]\ntopic = "{topic}"\n')


def setup_ntfy(home, want, out):
    """Whether ntfy ends up on. Creates the topic only when `secrets.toml` does not exist."""
    if not want:
        return False
    if paths.has_ntfy_topic(home):
        out("ntfy: secrets.toml already has a topic; keeping it")
        return True
    if (Path(home) / "secrets.toml").exists():
        out("ntfy: secrets.toml exists without a topic; add `[notify] topic = \"...\"` to it by hand. ntfy stays off")
        return False
    topic = ntfy_topic()
    write_topic(home, topic)
    out("ntfy: a private topic was created in secrets.toml (mode 600). It is shown only now:")
    out(f"  subscribe in the ntfy app to: https://ntfy.sh/{topic}")
    return True


# --- config --------------------------------------------------------------------------------

def write_config(home, language, enabled, notify, ask, out):
    """Creates `config.json`, or imports the TOML files as `config.load` does; an existing config is
    changed only when the owner says so. Returns what happened: "created", "imported", "updated" or "kept"."""
    path = Path(home) / config.FILE

    def apply(d):
        d["language"] = language
        for kind in config.ENGINES:
            d["engines"].setdefault(kind, config.empty()["engines"][kind])["enabled"] = kind in enabled
        d["notify"]["enabled"] = notify

    if not path.exists() and (Path(home) / "projects.toml").exists():
        config.load(home)  # the one-time import of projects.toml / claude.toml writes config.json
        out(f"imported projects.toml{' and claude.toml' if (Path(home) / 'claude.toml').exists() else ''} into {path}")
        state = "imported"
    elif not path.exists():
        config.save(home, apply)
        out(f"wrote {path}")
        return "created"
    else:
        state = "kept"
    current = config.load(home)
    on = [k for k in config.ENGINES if (current["engines"].get(k) or {}).get("enabled")]
    out(f"{path}: language {current.get('language', config.DEFAULT_LANGUAGE)}, engines on: {', '.join(on) or 'none'}, "
        f"ntfy {'on' if config.notify_enabled(current.get('notify')) else 'off'}")
    if ask.yes_no("Change it to your answers (language, engines, ntfy)?", False):
        config.save(home, apply)
        out(f"updated {path}")
        return "updated"
    return state


# --- panel ---------------------------------------------------------------------------------

def wait_for_port(port, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def open_url(url):
    if autopilot.platform_kind() == "wsl":
        _run(["cmd.exe", "/c", "start", "", url])
    else:
        webbrowser.open(url)


# --- the wizard ----------------------------------------------------------------------------

def run(args, home=None, ask=input, out=print):
    home = Path(home or paths.home())
    q = Asker(args.yes, ask, out)
    out("turno onboard: checking this machine")
    rows, found = checks()
    for name, ok, detail in rows:
        out(f"  {'ok ' if ok else '-- '} {name}: {detail}")
    if not found:
        out("no engine CLI found: install the Codex CLI or Claude Code, log in, and run `turno onboard` again")
        return 1

    language = args.language or q.choice("Language of the panel and of the phone texts", list(config.LANGUAGES), config.DEFAULT_LANGUAGE)
    if args.engines is not None:
        asked = [e.strip() for e in args.engines.split(",") if e.strip()]
        for e in asked:
            if e not in found:
                out(f"{e}: not found on this machine; not enabled")
        enabled = [e for e in found if e in asked]
    else:
        enabled = [e for e in found if q.yes_no(f"Enable {e}?", True)]
    want_ntfy = args.ntfy if args.ntfy is not None else q.yes_no("Send decisions to your phone with ntfy?", False)

    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    notify = setup_ntfy(home, want_ntfy, out)
    try:
        write_config(home, language, enabled, notify, q, out)
    except config.ConfigError as e:
        out(f"{config.FILE} is invalid, so it was left as it is: {e}")
        return 1

    kind = service.kind()
    installed = False
    if kind is None:
        out(service.manual_hint())
    elif args.service if args.service is not None else q.yes_no("Install the background service (panel and loops start at boot)?", True):
        # --yes never replaces an existing, different unit: it answers "no" itself instead of asking.
        service_ask = (lambda question: "n") if args.yes else ask
        installed = service.install(home, autopilot.turno_argv(), ask=service_ask, out=out) == 0
    url = f"http://localhost:{PORT}"
    answers = installed and wait_for_port(PORT)
    if installed and answers and service.status()["active"]:
        out(f"panel: {url}")
        if not args.no_open:
            open_url(url)
    elif installed:
        if answers:
            out(f"port {PORT} is in use by another process (a `turno serve` started by hand?); the service is not "
                f"running. Stop that process, then run `turno service status`")
        else:
            out("the service was installed but is not running: see `turno service status` "
                "and, with systemd, `journalctl --user -u turno`")
    else:
        out(f"start the panel with `turno serve --start-engines`, then open {url}")
    out("next: add your projects in the panel's settings (⚙)")
    return 0
