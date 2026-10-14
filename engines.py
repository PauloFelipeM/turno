"""Engines: how each CLI runs, reads the quota and reports how a run ended."""

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import schedule


def log(msg):
    """Replaced by autopilot.py's log on import."""
    print(msg, flush=True)


# --- generic -----------------------------------------------------------------------------


def window(limits, key):
    """(usage in %, resets_at) of a window; a window that already reset counts as 0%."""
    w = (limits or {}).get(key) or {}
    used, resets = float(w.get("used_percent") or 0), int(w.get("resets_at") or 0)
    if resets and resets <= time.time():
        return 0.0, 0
    return used, resets


def parse_events(lines):
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def read_events(path):
    try:
        with open(path, errors="replace") as f:
            return parse_events(f)
    except OSError:
        return []


def read_text(path):
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return ""


def tail_output(text, lines=20, chars=2000):
    """The end of a command's output, for the live feed."""
    out = "\n".join(str(text or "").splitlines()[-lines:])
    return out[-chars:]


def shell_command(cmd):
    """Strips the `/bin/bash -lc '...'` that Codex wraps around the command."""
    m = re.fullmatch(r"\S*/(?:ba)?sh -lc (['\"])(.*)\1", cmd or "", re.S)
    return m.group(2) if m else (cmd or "")


def failure_text(out, err):
    """Failure events of the JSONL plus stderr, and whether there was any failure event."""
    failures = [json.dumps(ev, ensure_ascii=False) for ev in read_events(out)
                if ev.get("type") in ("error", "turn.failed")]
    return "\n".join(failures) + "\n" + read_text(err), bool(failures)


def retry_at(text):
    """Time of the "try again at ..." in the limit message, as a timestamp; None if absent."""
    m = re.search(r"try again at (.*?\d{1,2}:\d{2}\s*[AP]M)", text, re.I)
    if not m:
        return None
    when = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", m.group(1)).strip()
    now = datetime.datetime.now()
    try:
        # Time only ("1:33 AM"): the next time it comes around.
        t = datetime.datetime.strptime(when.upper(), "%I:%M %p").time()
        at = datetime.datetime.combine(now.date(), t)
        if at <= now:
            at += datetime.timedelta(days=1)
        return at.timestamp()
    except ValueError:
        pass
    for fmt in ("%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p", "%b %d %Y %I:%M %p", "%B %d %Y %I:%M %p"):
        try:
            return datetime.datetime.strptime(when, fmt).timestamp()
        except ValueError:
            continue
    return None


# --- Codex -------------------------------------------------------------------------------------


def find_rate_limits(node):
    """Looks for the `rate_limits` key at any level of a session log event."""
    if isinstance(node, dict):
        if isinstance(node.get("rate_limits"), dict):
            return node["rate_limits"]
        node = list(node.values())
    if isinstance(node, list):
        for child in node:
            found = find_rate_limits(child)
            if found:
                return found
    return None


def has_windows(limits):
    # The log also has snapshots of other limits (e.g. "premium") with empty windows; taking
    # them as the current usage would make the quota look like 0% and hide the reset time.
    return isinstance(limits.get("primary"), dict) or isinstance(limits.get("secondary"), dict)


def snapshots(path):
    """Every `rate_limits` with windows in a session log, in order."""
    out = []
    try:
        with open(path, errors="replace") as f:
            for line in f:
                if '"rate_limits"' in line:
                    try:
                        found = find_rate_limits(json.loads(line))
                    except ValueError:
                        continue
                    if found and has_windows(found):
                        out.append(found)
    except OSError:
        pass
    return out


def locate_codex():
    """Path of the Codex CLI, or None when it is not installed."""
    if os.environ.get("CODEX_BIN"):
        return os.environ["CODEX_BIN"]
    found = shutil.which("codex")
    if found:
        return found
    # The VS Code extension ships the binary; use the newest installed version.
    exts = Path.home() / ".vscode-server" / "extensions"
    bins = sorted(exts.glob("openai.chatgpt-*/bin/linux-x86_64/codex"),
                  key=lambda p: [int(x) for x in re.findall(r"\d+", p.parts[-4])])
    if bins:
        return str(bins[-1])
    return None


def find_codex():
    found = locate_codex()
    if found is None:
        sys.exit("codex not found; set CODEX_BIN")
    return found



def thread_id(out):
    for ev in read_events(out):
        if ev.get("type") == "thread.started":
            return ev.get("thread_id")
    return None


class CodexEngine:
    name = "codex"
    label = "Codex"
    needs_limits = False

    QUIET = {"thread.started", "turn.started", "turn.completed"}

    @staticmethod
    def feed_items(ev):
        """One `codex exec --json` event -> live feed items.

        Codex does not say which subagent was called: only the wait for one shows up (`wait`).
        """
        t = ev.get("type")
        if t in ("error", "turn.failed"):
            msg = ev.get("message") or (ev.get("error") or {}).get("message") or ""
            return [{"kind": "error", "text": str(msg)}]
        if t in CodexEngine.QUIET:
            return []
        if t not in ("item.started", "item.completed"):
            return [{"kind": "unknown", "text": str(t)}]
        item = ev.get("item") or {}
        kind, done = item.get("type"), t == "item.completed"
        if kind == "agent_message":
            return [{"kind": "message", "text": item.get("text") or ""}] if done else []
        if kind == "command_execution":
            cmd = shell_command(item.get("command"))
            if not done:
                return [{"kind": "command", "text": cmd}]
            return [{"kind": "command_done", "text": cmd, "exit_code": item.get("exit_code"),
                     "output": tail_output(item.get("aggregated_output"))}]
        if kind == "file_change":
            return [{"kind": "file", "text": c.get("path") or ""} for c in item.get("changes") or []] if done else []
        if kind == "collab_tool_call":
            return [{"kind": "wait", "text": ""}] if not done and item.get("tool") == "wait" else []
        if kind == "error":
            return [{"kind": "error", "text": item.get("message") or ""}]
        return [{"kind": "unknown", "text": str(kind)}] if done else []

    def __init__(self, cfg, home=None):
        self.cfg = cfg
        self.home = home or Path.home() / ".codex"
        self.binary = None

    def _bin(self):
        # Looked up again if it disappears: the VS Code extension changes folder when it updates.
        if not self.binary or not Path(self.binary).exists():
            self.binary = find_codex()
        return self.binary

    def describe(self):
        d = self.cfg["defaults"]
        return f"codex={self._bin()}, orquestrador={d['orchestrator_model']}/{d['orchestrator_effort']}"

    def limits(self, state, max_age=None):
        """The latest snapshot among the newest sessions (includes your interactive use)."""
        sessions = self.home / "sessions"
        files = sorted(sessions.rglob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[:10]:
            snaps = snapshots(path)
            if snaps:
                return snaps[-1]
        return {}

    def resumable(self, s):
        return bool(s["session"])

    def begin(self, s, resume):
        pass  # the Codex session id only shows up in the output

    def command(self, state, project, s, resume, stem):
        d = self.cfg["defaults"]
        flags = ["--json", "-m", d["orchestrator_model"],
                 "-c", f'model_reasoning_effort="{d["orchestrator_effort"]}"',
                 "-c", f"agents.max_concurrent_threads_per_session={d['max_subagents']}",
                 "--enable", "multi_agent",
                 # Docker, network and installing dependencies require leaving the sandbox.
                 "--dangerously-bypass-approvals-and-sandbox"]
        codex = self._bin()
        if resume:
            return [codex, "exec", "resume", *flags, s["session"], "-"], None
        return [codex, "exec", *flags, "-"], None

    def after_run(self, state, s, out, resume):
        if not resume:
            s["session"] = thread_id(out)

    def classify(self, out, err, code):
        """('usage_limit' | 'error' | 'ok', reset time or None, whether the limit is hard)."""
        text, failed = failure_text(out, err)
        if re.search(r"usage limit|usage_limit|rate.?limit", text, re.I):
            hard = bool(re.search(r"usage limit|usage_limit", text, re.I))
            limits = self.limits(None)
            resets = [r for used, r in (window(limits, "primary"), window(limits, "secondary")) if used >= 90 and r]
            if not resets and (at := retry_at(text)):
                # No snapshot with the reset: use the time the error message itself gives.
                resets = [at]
            return "usage_limit", (max(resets) if resets else None), hard
        return ("error" if failed or code != 0 else "ok"), None, False

    def unit_cost(self, state, s):
        """Unit cost in percentage points, from the session's first to last snapshot."""
        thread = s["session"]
        files = list((self.home / "sessions").rglob(f"rollout-*{thread}*.jsonl")) if thread else []
        snaps = [x for f in files for x in snapshots(f)]
        if len(snaps) < 2:
            return None
        cost = {}
        for key in ("primary", "secondary"):
            a, b = (snaps[0].get(key) or {}), (snaps[-1].get(key) or {})
            # A reset in the middle of the unit invalidates that window's measure.
            if a.get("resets_at") != b.get("resets_at"):
                return None
            cost[key] = max(0.0, float(b.get("used_percent") or 0) - float(a.get("used_percent") or 0))
        return cost

    def close_unit(self, s):
        pass


# --- Claude ------------------------------------------------------------------------------------

ROLES = Path(__file__).resolve().parent / "claude-agents.json"
BASE_WINDOWS = {"five_hour", "seven_day"}
LIMIT_TEXT = re.compile(r"usage limit|rate limit|limit reached|out of (extra )?usage", re.I)
FABLE_LIMIT = re.compile(r"fable[^\n]{0,300}?(usage limit|rate limit|limit reached)"
                         r"|(usage limit|rate limit|limit reached)[^\n]{0,300}?fable", re.I)


def locate_claude():
    """Path of the Claude Code CLI, or None when it is not installed."""
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    found = shutil.which("claude") or (Path.home() / ".local" / "bin" / "claude")
    if Path(found).exists():
        return str(found)
    return None


def find_claude():
    found = locate_claude()
    if found is None:
        sys.exit("claude not found; set CLAUDE_BIN")
    return found


CLI_VERSION_TIMEOUT = 5  # seconds for `<bin> --version`
CLI_CACHE = {}  # engine -> (time.monotonic() of the reading, status)
LOCATORS = {"codex": locate_codex, "claude": locate_claude}


def cli_status(engine_name, max_age=60):
    """Whether the engine's CLI is installed and its version; never raises or exits.

    Cached per engine for `max_age` seconds, since the panel asks often.
    """
    locate = LOCATORS.get(engine_name)
    if locate is None:
        return {"found": False, "path": None, "version": None}
    cached = CLI_CACHE.get(engine_name)
    if cached and time.monotonic() - cached[0] < max_age:
        return dict(cached[1])
    path = locate()
    version = None
    if path:
        try:
            out = subprocess.run([path, "--version"], capture_output=True, text=True,
                                 timeout=CLI_VERSION_TIMEOUT, stdin=subprocess.DEVNULL).stdout
            lines = out.strip().splitlines()
            version = lines[0].strip() if lines else None
        except (OSError, subprocess.SubprocessError, ValueError):
            version = None
    status = {"found": path is not None, "path": path, "version": version}
    CLI_CACHE[engine_name] = (time.monotonic(), status)
    return dict(status)


def to_limits(info):
    """Claude's rate_limit_info -> the Codex format: primary (5 h), secondary (7 days), extra."""
    windows = info.get("unifiedWindows") or {}

    def convert(w):
        return {"used_percent": round(float(w.get("utilization") or 0) * 100, 1),
                "resets_at": int(w.get("resetsAt") or 0)}

    out = {}
    if isinstance(windows.get("five_hour"), dict):
        out["primary"] = convert(windows["five_hour"])
    if isinstance(windows.get("seven_day"), dict):
        out["secondary"] = convert(windows["seven_day"])
    extra = {k: convert(v) for k, v in windows.items() if k not in BASE_WINDOWS and isinstance(v, dict)}
    if extra:
        out["extra"] = extra
    return out


def rate_infos(events):
    return [e["rate_limit_info"] for e in events
            if e.get("type") == "rate_limit_event" and isinstance(e.get("rate_limit_info"), dict)]


def tool_result_texts(events):
    """Texts of tool results (including the subagents' answers), not the orchestrator's."""
    for e in events:
        if e.get("type") != "user":
            continue
        for c in (e.get("message") or {}).get("content") or []:
            if not isinstance(c, dict) or c.get("type") != "tool_result":
                continue
            content = c.get("content")
            if isinstance(content, str):
                yield content
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        yield part["text"]


class ClaudeEngine:
    name = "claude"
    label = "Claude"
    needs_limits = True  # without knowing the usage, the 90% cap cannot be honoured

    KNOWN = {"system", "user", "rate_limit_event", "tool_progress", "stream_event"}

    @staticmethod
    def feed_items(ev):
        """One `claude -p --output-format stream-json` event -> live feed items."""
        t = ev.get("type")
        if t == "result":
            return [{"kind": "error", "text": str(ev.get("result") or "")[:2000]}] if ev.get("is_error") else []
        if t != "assistant":
            return [] if t in ClaudeEngine.KNOWN else [{"kind": "unknown", "text": str(t)}]
        top = ev.get("parent_tool_use_id") is None  # subagent text is left out; its work is not
        out = []
        for c in (ev.get("message") or {}).get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text" and top and (c.get("text") or "").strip():
                out.append({"kind": "message", "text": c["text"]})
            elif c.get("type") == "tool_use":
                name, args = c.get("name"), c.get("input") or {}
                if name in ("Agent", "Task"):
                    out.append({"kind": "role", "role": args.get("subagent_type"), "text": args.get("description") or ""})
                elif name == "Bash":
                    out.append({"kind": "command", "text": args.get("command") or ""})
                elif name in ("Edit", "Write", "NotebookEdit"):
                    out.append({"kind": "file", "text": args.get("file_path") or args.get("notebook_path") or ""})
        return out

    def __init__(self, cfg, home=None, roles=None):
        self.cfg = cfg
        self.home = home or Path.home() / ".claude"
        self.roles = roles or ROLES
        self.binary = None

    def _bin(self):
        if not self.binary:
            self.binary = find_claude()
        return self.binary

    def describe(self):
        d = self.cfg["defaults"]
        return f"claude={self._bin()}, orquestrador={d['orchestrator_model']}/{d['orchestrator_effort']}"

    def _cache(self, state):
        return state.setdefault("_engine", {})

    def limits(self, state, max_age=1800):
        """The last quota reading; queries again if it is older than `max_age` seconds."""
        cache = self._cache(state)
        if time.time() - cache.get("limits_at", 0) > max_age and not self.probe(state):
            return {}  # the query failed: an old reading is no basis for deciding
        return cache.get("limits") or {}

    def probe(self, state):
        """A minimal Haiku query, without plugins or hooks, only to read the quota event. True if read."""
        cmd = [self._bin(), "-p", "ok", "--model", "haiku", "--max-turns", "1", "--safe-mode",
               "--no-session-persistence", "--output-format", "stream-json", "--verbose"]
        try:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired) as e:
            log(f"Claude quota query failed: {e}")
            return False
        infos = rate_infos(parse_events(r.stdout.splitlines()))
        if not infos:
            log(f"Claude quota query without a quota event (exit={r.returncode})")
            return False
        self.record(state, infos)
        return True

    def record(self, state, infos):
        cache = self._cache(state)
        merged = {}
        for info in infos:
            limits = to_limits(info)
            extra = limits.pop("extra", {})
            merged.update(limits)
            merged.setdefault("extra", {}).update(extra)
        cache.update(limits=merged, limits_at=time.time())
        # A window beyond the general ones = Fable's own quota (it shows up in runs with Fable). It
        # has a cap like the weekly one: planning moves to Opus before it runs out, so the owner
        # keeps some Fable. Claude Code's own warning (allowed_warning) also counts.
        cap = float(self.cfg.get("defaults", {}).get("fable_cap", 80))
        for info in infos:
            warned = info.get("status") in ("allowed_warning", "rejected")
            for key, w in to_limits(info).get("extra", {}).items():
                if w["resets_at"] <= time.time():
                    continue
                if w["used_percent"] >= cap:
                    self._fable_out(state, w["resets_at"], f"window {key} at {w['used_percent']:.0f}%, cap {cap:.0f}%")
                elif warned:
                    self._fable_out(state, w["resets_at"], f"Claude Code warning with window {key} at {w['used_percent']:.0f}%")

    def _fable_out(self, state, until, why):
        cache = self._cache(state)
        if cache.get("fable_exhausted_until", 0) < until:
            cache["fable_exhausted_until"] = until
            log(f"Fable spared until {time.strftime('%F %T', time.localtime(until))} ({why}); "
                f"planner and plan-reviewer move to Opus")

    def fable_available(self, state):
        return self._cache(state).get("fable_exhausted_until", 0) <= time.time()

    def resumable(self, s):
        sid = s.get("session")
        return bool(sid) and any((self.home / "projects").glob(f"*/{sid}.jsonl"))

    def begin(self, s, resume):
        if not resume:
            # The id is chosen here and saved before the CLI starts: a cut never loses it.
            s["session"] = str(uuid.uuid4())

    def write_agents(self, state, path):
        roles = json.loads(Path(self.roles).read_text())
        fable_ok = self.fable_available(state)
        out = {}
        for name, role in roles.items():
            role = dict(role)
            fallback = role.pop("fallback_model", None)
            if fallback and not fable_ok:
                role["model"] = fallback
            out[name] = role
        Path(path).write_text(json.dumps(out, indent=2, ensure_ascii=False))

    def command(self, state, project, s, resume, stem):
        d = self.cfg["defaults"]
        agents = Path(f"{stem}-agents.json")
        self.write_agents(state, agents)
        cmd = [self._bin(), "-p", "--output-format", "stream-json", "--verbose",
               "--model", d["orchestrator_model"], "--effort", d["orchestrator_effort"],
               "--agents", str(agents), "--dangerously-skip-permissions"]
        cmd += ["--resume", s["session"]] if resume else ["--session-id", s["session"]]
        return cmd, None

    def after_run(self, state, s, out, resume):
        events = read_events(out)
        infos = rate_infos(events)
        if infos:
            self.record(state, infos)
            if s.get("unit_start_limits") is None:
                s["unit_start_limits"] = to_limits(infos[0])
        if any(FABLE_LIMIT.search(t) for t in tool_result_texts(events)):
            extra = (self._cache(state).get("limits") or {}).get("extra") or {}
            until = max((w["resets_at"] for w in extra.values()), default=0)
            if until <= time.time():
                until = schedule.next_occurrence("sun 04:00", datetime.datetime.now()).timestamp()
            self._fable_out(state, until, "limit message in a subagent")

    def classify(self, out, err, code):
        events = read_events(out)
        result = next((e for e in reversed(events) if e.get("type") == "result"), None)
        if result and not result.get("is_error") and code == 0:
            return "ok", None, False  # ended fine: a "rejected" event of another model does not count
        # Only a rejection that exhausted the general windows counts: another model's (a subagent's
        # Fable) does not stop the orchestrator from going on.
        resets = []
        for info in rate_infos(events):
            if info.get("status") != "rejected":
                continue
            windows = info.get("unifiedWindows") or {}
            if not windows:
                resets.append(int(info.get("resetsAt") or 0))
            for key in BASE_WINDOWS:
                w = windows.get(key) or {}
                if float(w.get("utilization") or 0) >= 0.98:
                    resets.append(int(w.get("resetsAt") or info.get("resetsAt") or 0))
        text = str((result or {}).get("result") or "") + "\n" + read_text(err)
        if resets or LIMIT_TEXT.search(text):
            reset = max(resets or [0])
            if not reset and (m := re.search(r"limit reached\|(\d{9,})", text)):
                reset = int(m.group(1))
            if not reset:
                reset = retry_at(text) or 0
            return "usage_limit", (reset or None), True
        return "error", None, False

    def unit_cost(self, state, s):
        """Usage after the unit (fresh query) minus usage at its start, per window."""
        start = s.get("unit_start_limits")
        if not self.probe(state):
            return None  # without a fresh reading the "end" would be the start itself and the cost would be zero
        end = self._cache(state).get("limits")
        if not start or not end:
            return None
        cost = {}
        for key in ("primary", "secondary"):
            a, b = start.get(key) or {}, end.get(key) or {}
            if not a or not b or a.get("resets_at") != b.get("resets_at"):
                return None
            cost[key] = max(0.0, b["used_percent"] - a["used_percent"])
        return cost

    def close_unit(self, s):
        s["unit_start_limits"] = None


ENGINES = {"codex": CodexEngine, "claude": ClaudeEngine}


def make_engine(cfg):
    kind = (cfg.get("engine") or {}).get("kind", "codex")
    if kind not in ENGINES:
        sys.exit(f"unknown engine in [engine] kind: {kind}")
    return ENGINES[kind](cfg)
