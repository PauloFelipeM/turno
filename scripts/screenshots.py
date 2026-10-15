#!/usr/bin/env python3
"""Regenerates the panel screenshots in docs/images/ from demo data.

    npm --prefix ui run build          # the panel must be built first
    python3 scripts/screenshots.py [--only panel run ...] [--delay 6] [--chrome PATH]

What it does, and cleans up at the end:

1. builds a throwaway data folder (`TURNO_HOME`) with made-up projects, history, decisions and a
   live run, and a fake loop process (so the panel shows an engine as running);
2. copies the `turno` package, with its built panel, to a temporary folder and injects a "scene"
   script into the copy's index.html (nothing is added to `ui/` or to the real panel);
3. serves that copy with `python -m turno serve` on a free port;
4. runs headless Chrome once per scene (`?scene=<name>`) and saves a PNG in docs/images/.

The demo data lives in a temporary folder, so the scene script rewrites that folder's path to
`/home/me` in the API answers before the panel draws them. The fake CLIs are scripts in the same
folder. Headless Chrome takes its screenshot on the `load` event, so every page also loads an
image from a small server of this script that answers only after `--delay` seconds: that is the
time the scene gets to open its dialog and scroll.

Standard library only. Never touches ~/.turno, ~/.codex or any real project.
"""

import argparse
import atexit
import http.server
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from turno import config, history  # noqa: E402  (after the sys.path line)

OUT = ROOT / "docs" / "images"

# scene -> (window width, window height). The heights fit each page's content.
SCENES = {
    "panel": (1400, 1790),
    "panel-dark": (1400, 1790),
    "run": (1100, 1180),
    "settings-projects": (780, 880),
    "settings-engines": (780, 960),
    "settings-general": (780, 880),
}
WIN_CHROME = "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
UNIT_DIALOG = "u-demo-3"  # the unit `run.png` opens

HERE = Path(__file__).resolve()
cleanups = []  # callables, run in reverse order at exit


def cleanup():
    while cleanups:
        try:
            cleanups.pop()()
        except Exception as e:  # keep cleaning whatever happens
            print(f"cleanup: {e}", file=sys.stderr)


atexit.register(cleanup)


# --- demo data -----------------------------------------------------------------------------------

def jl(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events))


class Run:
    """Builds the JSONL of a Codex run."""

    def __init__(self):
        self.events, self.n = [], 0

    def _item(self, kind, done, **kw):
        self.n += 1
        self.events.append({"type": "item.completed" if done else "item.started",
                            "item": {"id": f"item_{self.n}", "type": kind, **kw}})

    def say(self, text):
        self._item("agent_message", True, text=text)
        return self

    def cmd(self, command, output="", code=0):
        wrapped = f"/bin/bash -lc '{command}'"
        self._item("command_execution", False, command=wrapped, aggregated_output="", exit_code=None, status="in_progress")
        self._item("command_execution", True, command=wrapped, aggregated_output=output, exit_code=code, status="completed")
        return self

    def edit(self, root, *paths):
        changes = [{"path": f"{root}/{p}", "kind": "update"} for p in paths]
        self._item("file_change", False, changes=changes, status="in_progress")
        self._item("file_change", True, changes=changes, status="completed")
        return self

    def wait(self, done=True):
        self._item("collab_tool_call", False, tool="wait", status="in_progress")
        if done:
            self._item("collab_tool_call", True, tool="wait", status="completed")
        return self

    def error(self, message):
        self.events.append({"type": "error", "message": message})
        return self


def live_run(root):
    return (Run()
            .say("Resuming task 6 of 9: totals and rounding for the invoice export. Reading the plan first.")
            .cmd("git status", "On branch main\nnothing to commit, working tree clean")
            .cmd("sed -n 1,40p docs/plans/invoice-export.md", "# Invoice export\n\n## Task 6: totals and rounding\n- sum line items per currency\n- round half up to 2 decimals")
            .say("Task 6 only touches the totals module. I will hand it to the implementer and review the diff.")
            .wait()
            .edit(root, "src/invoices/totals.ts", "src/invoices/totals.test.ts")
            .cmd("npm test -- totals", "FAIL src/invoices/totals.test.ts\n  x rounds half up (4 ms)\n\nTests: 1 failed, 11 passed, 12 total", 1)
            .edit(root, "src/invoices/totals.ts")
            .cmd("npm test", "Test Suites: 9 passed, 9 total\nTests:       48 passed, 48 total", 0)
            .cmd("npm run lint", "No lint errors found", 0)
            .cmd("git status", " M src/invoices/totals.test.ts\n M src/invoices/totals.ts", 0)
            .say("Tests and lint pass. The reviewer checks the diff before I commit.")
            .wait(done=False)).events


def dialog_runs(root):
    """The two runs of the unit `run.png` opens: cut by a usage limit, then resumed."""
    first = (Run()
             .say("Task 3 of 7: token bucket middleware for the rate limiter.")
             .cmd("git status", "On branch main\nnothing to commit, working tree clean")
             .cmd("grep -rn middleware api/ --include=*.py", "api/app.py:12:from api.middleware import request_id\napi/middleware.py:5:def request_id(app):")
             .wait()
             .edit(root, "api/ratelimit.py", "tests/test_ratelimit.py")
             .cmd("python -m pytest tests/test_ratelimit.py -q", "..F\n1 failed, 2 passed in 0.31s", 1)
             .error("You have hit your usage limit. Try again at 4:12 PM.")).events
    second = (Run()
              .say("Resuming task 3 of 7 from the ledger: the rate limiter test still fails.")
              .cmd("git status", " M api/ratelimit.py\n?? tests/test_ratelimit.py")
              .cmd("git diff --stat", " api/ratelimit.py | 41 +++++++++++++\n 1 file changed, 41 insertions(+)")
              .cmd("python -m pytest tests/test_ratelimit.py -q", "..F\nFAILED tests/test_ratelimit.py::test_refill - assert 3 == 4\n1 failed, 2 passed in 0.29s", 1)
              .say("The bucket refills on a float timestamp. I will ask the implementer to switch to a monotonic clock.")
              .wait()
              .edit(root, "api/ratelimit.py")
              .cmd("python -m pytest -q", "......................\n22 passed in 1.84s", 0)
              .edit(root, "tests/test_ratelimit.py")
              .cmd("python -m pytest -q", "......................\n23 passed in 1.90s", 0)
              .cmd("ruff check .", "All checks passed!", 0)
              .say("Green. The reviewer subagent goes over the diff.")
              .wait()
              .cmd("git diff --stat", " api/ratelimit.py           | 44 +++++++++++++++\n tests/test_ratelimit.py    | 31 ++++++++++++\n 2 files changed, 75 insertions(+)", 0)
              .say("Review found no blocking issues. Committing task 3.")
              .cmd("git add -A", "", 0)
              .cmd("git commit -m feat-rate-limiter-token-bucket", "[main 4f3a9c1] feat-rate-limiter-token-bucket\n 2 files changed, 75 insertions(+)", 0)
              .cmd("git status", "On branch main\nnothing to commit, working tree clean", 0)
              .say("Task 3 done. Ledger and backlog updated; the next unit starts task 4.")).events
    return first, second


def build_home(base, now):
    """Creates the demo world under `base`; returns the dict of paths and the fake loop's pid file target."""
    me = base / "me"  # shown as /home/me
    data = base / "turno-home"
    codex_home = base / "codex-home"
    bindir = me / ".local" / "bin"
    bindir.mkdir(parents=True)
    for name, version in (("codex", "codex-cli 0.58.0"), ("claude", "2.0.30 (Claude Code)")):
        exe = bindir / name
        exe.write_text(f'#!/bin/sh\necho "{version}"\n')
        exe.chmod(0o755)

    projects = {}
    for name in ("my-app", "my-api", "side-project"):
        p = projects[name] = me / "projects" / name
        (p / ".superpowers" / "autopilot" / "decisions").mkdir(parents=True)
        (p / "docs").mkdir()
        (p / "docs" / "autopilot.md").write_text("# Autopilot rules\n")
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        subprocess.run(["git", "init", "-q", str(p)], check=True, env=env, capture_output=True)
        with open(p / ".git" / "info" / "exclude", "a") as f:
            f.write(".superpowers/\n")

    # config.json
    cfg = config.empty()
    cfg["language"] = "en"
    cfg["update_check"] = False
    cfg["notify"] = {"enabled": True, "server": "https://ntfy.sh", "decision_timeout_hours": 4}
    caps = {"my-app": {"codex": 85, "claude": 80}, "my-api": {"codex": 70, "claude": 65}, "side-project": {"codex": 50, "claude": 45}}
    cfg["projects"] = [{"name": n, "path": str(projects[n]), "weekly_cap": caps[n], **({"docker_group": "web"} if n != "side-project" else {})}
                       for n in ("my-app", "my-api", "side-project")]
    for k in ("codex", "claude"):
        cfg["engines"][k]["enabled"] = True
        cfg["engines"][k]["primary_cap"] = 90
    cfg["engines"]["claude"]["schedule"] = {"start": "fri 19:00", "end": "sun 03:00", "start_margin_minutes": 15}
    data.mkdir(parents=True)
    (data / "config.json").write_text(json.dumps(cfg, indent=2))
    (data / "secrets.toml").write_text('[notify]\ntopic = "demo-only"\n')  # the panel only learns that a topic exists
    config.load(data)  # fails here, not in the browser, if the demo config is invalid

    def backlog(name, items):
        (projects[name] / ".superpowers" / "autopilot" / "backlog.json").write_text(json.dumps({"items": items}))

    backlog("my-app", [
        {"id": "auth", "title": "Sign-in and sessions", "state": "done"},
        {"id": "list", "title": "Invoice list and filters", "state": "done"},
        {"id": "export", "title": "Invoice export (CSV and PDF)", "state": "doing", "tasks": {"done": 5, "total": 9}},
        {"id": "remind", "title": "Payment reminders by email", "state": "todo"},
        {"id": "dash", "title": "Dashboard widgets", "state": "todo"}])
    backlog("my-api", [
        {"id": "docs", "title": "OpenAPI documentation", "state": "done"},
        {"id": "rate", "title": "Rate limiting per API key", "state": "plan"},
        {"id": "hooks", "title": "Webhook delivery and retries", "state": "todo"},
        {"id": "audit", "title": "Audit log", "state": "todo"}])
    backlog("side-project", [
        {"id": "offline", "title": "Offline mode and sync", "state": "spec"},
        {"id": "share", "title": "Share a list by link", "state": "todo"},
        {"id": "theme", "title": "Theme picker", "state": "todo"}])

    # an open decision
    decision = {
        "id": "sync-conflicts",
        "question": "When two devices edit the same list offline, which edit should win?",
        "context": "Offline mode merges changes on reconnect. Last write wins is simple but can drop an edit; "
                   "per-item merge keeps both and costs about two more tasks.",
        "options": [{"id": "a", "label": "Last write wins"},
                    {"id": "b", "label": "Merge per item, newest edit wins on a clash"},
                    {"id": "c", "label": "Keep both copies and ask the user"}],
        "default": "b", "notified_at": now - 3600, "deadline": now + 3 * 3600}
    dec_dir = projects["side-project"] / ".superpowers" / "autopilot" / "decisions"
    (dec_dir / "sync-conflicts.json").write_text(json.dumps(decision))

    # overrides: a paused project and the one chosen to run next
    (data / "overrides.json").write_text(json.dumps({"projects": {"side-project": {"paused": True}}, "next": "my-api", "poke": now}))

    # quota readings
    session = codex_home / "sessions" / "2026" / "10" / "15" / "rollout-demo.jsonl"
    session.parent.mkdir(parents=True)
    session.write_text(json.dumps({"payload": {"rate_limits": {
        "primary": {"used_percent": 38.0, "resets_at": int(now + 2 * 3600 + 14 * 60)},
        "secondary": {"used_percent": 35.0, "resets_at": int(now + 3 * 86400 + 5 * 3600)}}}}) + "\n")

    run_id = "r-demo-live"
    state = {
        "my-app": {"status": "active", "interrupted": False, "unit_id": "u-demo-live", "run": {"id": run_id, "started": now - 17 * 60},
                   "cost": {"primary": 6.5, "secondary": 1.4}},
        "my-api": {"status": "active", "interrupted": False, "cost": {"primary": 7.0, "secondary": 1.8}},
        "side-project": {"status": "waiting", "interrupted": False, "cost": {"primary": 5.0, "secondary": 1.1}},
        "_loop": {"phase": "running", "project": "my-app", "until": None}}
    (data / "state.json").write_text(json.dumps(state))
    (data / "state-claude.json").write_text(json.dumps({
        "my-app": {"status": "active", "cost": {"primary": 9.0, "secondary": 2.0}},
        "_engine": {"limits": {"primary": {"used_percent": 12.0, "resets_at": int(now + 3 * 3600 + 20 * 60)},
                               "secondary": {"used_percent": 27.0, "resets_at": int(now + 4 * 86400)}},
                    "limits_at": now}}))
    jl(data / "runs" / f"{run_id}.jsonl", live_run(str(projects["my-app"])))

    # history
    def entry(uid, run, project, engine, outcome, ago, minutes, closed=True, **extra):
        start = now - ago
        return {"unit_id": uid, "run": run, "project": project, "engine": engine, "outcome": outcome, "closed": closed,
                "started_at": history.iso(start), "ended_at": history.iso(start + minutes * 60), **extra}

    h = 3600
    codex = [
        entry("u-demo-1", "r-demo-1", "my-app", "codex", "worked", 1.2 * h, 21,
              unit="Task 5 of 9: CSV export of the invoice list", milestone="Invoice export",
              summary="Added the CSV endpoint and a download button on the invoice list. 12 new tests, all green.",
              cost={"primary": 6.2, "secondary": 1.3}),
        entry("u-demo-2", "r-demo-2", "side-project", "codex", "waiting", 3 * h, 14,
              unit="Spec for offline mode", summary="The spec needs a decision about sync conflicts. Asked the owner.",
              cost={"primary": 3.0, "secondary": 0.6}),
        entry("u-demo-3", "r-demo-3a", "my-api", "codex", "usage_limit", 5.6 * h, 12, closed=False),
        entry("u-demo-3", "r-demo-3b", "my-api", "codex", "worked", 4.4 * h, 24,
              unit="Task 3 of 7: token bucket middleware", milestone="Rate limiting per API key",
              summary="Token bucket per API key with a monotonic clock. Resumed after a usage limit; 23 tests pass.",
              cost={"primary": 9.4, "secondary": 2.1}),
        entry("u-demo-4", "r-demo-4", "my-app", "codex", "error", 9 * h, 4,
              unit="Task 4 of 9: PDF layout", summary="The Docker stack did not come up; retried later.",
              cost={"primary": 0.4, "secondary": 0.1}),
        entry("u-demo-6", "r-demo-6", "side-project", "codex", "idle", 28 * h, 3,
              unit="Backlog review", summary="Nothing ready: every item waits for a spec.",
              cost={"primary": 0.8, "secondary": 0.2}),
    ]
    claude = [
        entry("u-demo-5", "r-demo-5", "my-app", "claude", "worked", 3.2 * 24 * h, 41,
              unit="Task 2 of 9: invoice PDF template", milestone="Invoice export",
              summary="PDF template with the company header and line item table.", cost={"primary": 14.0, "secondary": 3.1}),
    ]
    for rows, eng in ((codex, "codex"), (claude, "claude")):
        with open(data / f"history-{eng}.jsonl", "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    first, second = dialog_runs(str(projects["my-api"]))
    jl(data / "runs" / "r-demo-3a.jsonl", first)
    jl(data / "runs" / "r-demo-3b.jsonl", second)
    jl(data / "runs-claude" / "r-demo-5.jsonl", [])
    (data / "shared" / "claims").mkdir(parents=True)
    return {"me": me, "data": data, "codex_home": codex_home, "bindir": bindir, "projects": projects}


# --- processes -----------------------------------------------------------------------------------

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def stop_group(proc):
    """Ends a process we started with start_new_session=True and its children, by pid (never by name)."""
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def start_fake_loop(tmp, data):
    """A process whose command line looks like the Codex loop, with its pid in the instance lock."""
    script = tmp / "loop" / "autopilot.py"
    script.parent.mkdir()
    script.write_text("import time\ntime.sleep(3600)\n")
    proc = subprocess.Popen([sys.executable, str(script), "run"], start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cleanups.append(lambda: stop_group(proc))
    (data / "shared" / "instance-codex.lock").write_text(str(proc.pid))
    return proc


SCENE_JS = r"""
(function () {
  var params = new URLSearchParams(location.search);
  var scene = params.get("scene") || "panel";
  var TMP = %(tmp)s, SLOW = %(slow)s, UNIT = %(unit)s;
  try { localStorage.setItem("turno.theme", scene === "panel-dark" ? "dark" : "light"); } catch (e) {}

  // The demo data lives in a temporary folder: show it as /home/me.
  var realFetch = window.fetch.bind(window);
  window.fetch = function () {
    return realFetch.apply(null, arguments).then(function (r) {
      if ((r.headers.get("content-type") || "").indexOf("json") < 0) return r;
      return r.text().then(function (t) {
        return new Response(t.split(TMP).join("/home/me"), { status: r.status, headers: r.headers });
      });
    });
  };

  function sleep(ms) { return new Promise(function (ok) { setTimeout(ok, ms); }); }
  function until(fn, ms) {
    var t0 = Date.now();
    return new Promise(function (ok, no) {
      (function tick() {
        var v = null;
        try { v = fn(); } catch (e) {}
        if (v) return ok(v);
        if (Date.now() - t0 > (ms || 12000)) return no(new Error("timeout waiting for " + fn));
        setTimeout(tick, 80);
      })();
    });
  }
  function q(sel) { return document.querySelector(sel); }
  function tab(name) {
    var el = q('[data-sf="tab:' + name + '"]');
    el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, button: 0 }));
  }
  async function settings(name) {
    await until(function () { return q('[data-action="settings-open"]'); });
    q('[data-action="settings-open"]').click();
    await until(function () { return q('[data-sf="tab:' + name + '"]'); });
    tab(name);
    await until(function () { return q('[data-state="active"][role="tabpanel"] *'); });
  }

  async function main() {
    await until(function () { return q('[data-section="history"] tr[data-unit]') && q("[data-run] li") && q('[data-section="attention"]'); });
    await sleep(500);
    if (scene === "run") {
      q('tr[data-unit="' + UNIT + '"]').click();
      var ol = await until(function () { return q('[data-dialog="unit"] ol'); });
      await sleep(400);
      var scrollers = document.querySelectorAll('[data-dialog="unit"] ol');
      scrollers.forEach(function (o) { o.scrollTop = 0; });
      document.activeElement && document.activeElement.blur();
    } else if (scene.indexOf("settings-") === 0) {
      var name = scene.slice(9);
      await settings(name);
      await sleep(400);
      if (name === "engines") {
        var block = await until(function () { return q('[data-settings-engine="claude"]'); });
        // Only the tab's own scroll area moves: scrollIntoView would also scroll the dialog away from its title.
        var box = block.closest(".overflow-y-auto");
        box.scrollTop += block.getBoundingClientRect().top - box.getBoundingClientRect().top - 12;
      }
      document.activeElement && document.activeElement.blur();
    }
    await sleep(600);
  }
  main().catch(function (e) { new Image().src = SLOW.replace("slow.png", "fail") + "?m=" + encodeURIComponent(String(e)); });
})();
"""

PIXEL = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")


class SlowServer:
    """Answers /slow.png only after `delay` seconds (so Chrome's load event waits for the scene)."""

    def __init__(self, delay):
        outer = self
        self.failures = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/fail"):
                    outer.failures.append(self.path)
                    self.send_response(204)
                    self.end_headers()
                    return
                time.sleep(delay)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(PIXEL)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(PIXEL)
                except OSError:
                    pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        cleanups.append(self.httpd.shutdown)
        cleanups.append(self.httpd.server_close)


# --- Chrome --------------------------------------------------------------------------------------

def is_wsl():
    return "microsoft" in platform.uname().release.lower() or bool(os.environ.get("WSL_DISTRO_NAME"))


def find_chrome(explicit=None):
    """(path, is_windows_binary_under_wsl)."""
    if explicit:
        return explicit, explicit.lower().endswith(".exe")
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found, False
    for path in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Chromium.app/Contents/MacOS/Chromium"):
        if Path(path).exists():
            return path, False
    if is_wsl() and Path(WIN_CHROME).exists():
        return WIN_CHROME, True
    sys.exit("Chrome not found: install Google Chrome or Chromium, or pass --chrome PATH")


def wsl_temp():
    """The Windows %TEMP% as a WSL path."""
    out = subprocess.run(["cmd.exe", "/c", "echo %TEMP%"], capture_output=True, text=True, cwd="/mnt/c", timeout=30).stdout
    win = out.strip().splitlines()[-1].strip()
    return Path(subprocess.run(["wslpath", "-u", win], capture_output=True, text=True, check=True).stdout.strip())


def wslpath_w(path):
    return subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, check=True).stdout.strip()


def shoot(chrome, windows, work, scene, url, size):
    """Runs Chrome on `url` and returns the PNG's path (inside `work`)."""
    png = work / f"{scene}.png"
    profile = work / "profile"
    arg = (lambda p: wslpath_w(p)) if windows else str
    cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
           "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-background-networking",
           f"--user-data-dir={arg(profile)}", f"--window-size={size[0]},{size[1]}", f"--screenshot={arg(png)}", url]
    if os.name == "posix" and os.geteuid() == 0:
        cmd.insert(1, "--no-sandbox")
    subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=120, cwd=work if not windows else "/mnt/c")
    if not png.exists() or png.stat().st_size < 1000:
        raise RuntimeError(f"Chrome produced no screenshot for {scene}")
    return png


# --- main ----------------------------------------------------------------------------------------

def wait_http(url, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"the panel did not answer at {url}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", nargs="+", choices=list(SCENES), help="regenerate only these scenes")
    ap.add_argument("--delay", type=float, default=6.0, help="seconds the slow image holds the load event (default 6)")
    ap.add_argument("--chrome", help="path to Chrome or Chromium")
    args = ap.parse_args()

    if not (ROOT / "turno" / "web" / "index.html").is_file():
        sys.exit("The panel is not built. Run: npm --prefix ui ci && npm --prefix ui run build")
    chrome, windows = find_chrome(args.chrome)

    tmp = Path(tempfile.mkdtemp(prefix="turno-shots-"))
    cleanups.append(lambda: shutil.rmtree(tmp, ignore_errors=True))
    now = time.time()
    world = build_home(tmp, now)
    start_fake_loop(tmp, world["data"])

    # The package and its panel, copied; the scene script goes into the copy only.
    pkg = tmp / "pkg"
    shutil.copytree(ROOT / "turno", pkg / "turno", ignore=shutil.ignore_patterns("__pycache__"))
    slow = SlowServer(args.delay)
    tmp_root = str(world["me"])
    page = pkg / "turno" / "web" / "index.html"
    scene_js = SCENE_JS % {"tmp": json.dumps(tmp_root), "unit": json.dumps(UNIT_DIALOG),
                           "slow": json.dumps(f"http://127.0.0.1:{slow.port}/slow.png")}
    html = page.read_text()
    html = html.replace("<head>", f"<head>\n<script>{scene_js}</script>", 1)
    html = html.replace("</body>", f'<img src="http://127.0.0.1:{slow.port}/slow.png" alt="" width="1" height="1" '
                                   f'style="position:fixed;left:-9px;top:-9px"></body>', 1)
    page.write_text(html)

    port = free_port()
    if port == 8765:
        port = free_port()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(world["me"]), "TURNO_HOME": str(world["data"]),
           "CODEX_HOME": str(world["codex_home"]), "CODEX_BIN": str(world["bindir"] / "codex"),
           "CLAUDE_BIN": str(world["bindir"] / "claude"), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "LANG": "C.UTF-8", "TZ": "UTC"}
    server = subprocess.Popen([sys.executable, "-m", "turno", "serve", "--port", str(port)], cwd=pkg, env=env,
                              start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cleanups.append(lambda: stop_group(server))
    wait_http(f"http://127.0.0.1:{port}/api/overview")
    if windows:
        time.sleep(3)  # WSL forwards the port to Windows a moment after it opens

    work_parent = wsl_temp() if windows else None
    work = Path(tempfile.mkdtemp(prefix="turno-chrome-", dir=work_parent))
    cleanups.append(lambda: shutil.rmtree(work, ignore_errors=True))
    OUT.mkdir(parents=True, exist_ok=True)

    failed = []
    for scene in args.only or list(SCENES):
        print(f"{scene} ...", flush=True)
        slow.failures.clear()
        shutil.rmtree(work / "profile", ignore_errors=True)
        png = shoot(chrome, windows, work, scene, f"http://localhost:{port}/?scene={scene}", SCENES[scene])
        if slow.failures:
            failed.append(scene)
            print(f"  the scene script failed: {slow.failures}", file=sys.stderr)
        shutil.copyfile(png, OUT / f"{scene}.png")
        png.unlink()
        print(f"  docs/images/{scene}.png ({(OUT / f'{scene}.png').stat().st_size // 1024} KB)")
    if failed:
        sys.exit(f"scenes that failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
