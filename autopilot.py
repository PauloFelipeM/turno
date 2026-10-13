#!/usr/bin/env python3
"""turno: runs the Codex and Claude Code CLIs unattended on several projects that share one quota.

Runs one "unit" of work at a time (a task, a spec, a plan, finishing an item), always in a short
CLI session. Between units it:

- first finishes the unit that was interrupted (usage limit, error, early end of the response);
- picks the highest-priority project whose weekly usage still fits under its `weekly_cap`;
- only starts when the 5-hour and weekly windows have room for the unit's learned average cost;
- sleeps until the reset when there is no room.

Product decisions that matter become a question on the phone (ntfy), with one button per option.
The project waits for the answer until the deadline; without an answer the default option applies.
Meanwhile the other projects keep going.

Usage:
  autopilot.py start                      start both loops (Codex and Claude) in the tmux session "autopilot"
  autopilot.py stop                       stop both loops (like a Ctrl+C in each)
  autopilot.py run                        a single loop, the Codex one (Ctrl+C stops; running again resumes)
  autopilot.py status                     each project's state and the current window usage
  autopilot.py check                      what the loop would do now (reads the quota; runs nothing)
  autopilot.py reset NAME                 put an idle or blocked project back to active
  autopilot.py decisions                  pending decisions
  autopilot.py answer NAME ID OPTION      answer a decision from the terminal
  autopilot.py test-notify                send a test notification
  autopilot.py serve [--port 8765]        the panel at http://localhost:8765 (read-only; safe)
  autopilot.py --config claude.toml run   the Claude instance (see README)
"""

import argparse
import datetime
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

import backlog
import coord
import engines
import history
import overrides
import paths
import schedule

BASE = Path(__file__).resolve().parent  # code: prompts, roles, interface
HOME = paths.home()  # data: config, state, logs, runs
CONFIG = HOME / "projects.toml"
# The ntfy topic is a secret: it lives here, outside git, and both instances use it.
SECRETS = HOME / "secrets.toml"
STATE = HOME / "state.json"
LOG = HOME / "autopilot.log"
RUNS = HOME / "runs"
HISTORY = HOME / "history-codex.jsonl"
PROMPTS = BASE / "prompts"
UNIT_FILE = Path(".superpowers") / "autopilot" / "last-unit.json"
DECISIONS = Path(".superpowers") / "autopilot" / "decisions"
KEEP_RUNS = 80
EMA_ALPHA = 0.3


def log(msg):
    line = f"{time.strftime('%F %T')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


engines.log = log
from engines import window  # noqa: E402  (used to pick the project and in status)


def resolve_config(config_path):
    """A relative path is resolved from the data folder; no path means projects.toml."""
    if not config_path:
        return HOME / "projects.toml"
    path = Path(config_path).expanduser()
    return path if path.is_absolute() else HOME / path


def configure(config_path=None):
    """Points CONFIG, STATE, LOG and RUNS at the instance of the config file.

    Without [instance] name the usual names apply; with name = "claude", state-claude.json,
    autopilot-claude.log and runs-claude/. All inside the data folder.
    """
    global CONFIG, STATE, LOG, RUNS, HISTORY
    HOME.mkdir(parents=True, exist_ok=True)
    CONFIG = resolve_config(config_path)
    cfg = load_config()
    name = (cfg.get("instance") or {}).get("name")
    suffix = f"-{name}" if name else ""
    STATE = HOME / f"state{suffix}.json"
    LOG = HOME / f"autopilot{suffix}.log"
    RUNS = HOME / f"runs{suffix}"
    HISTORY = HOME / f"history-{(cfg.get('engine') or {}).get('kind', 'codex')}.jsonl"


def load_config():
    with CONFIG.open("rb") as f:
        cfg = tomllib.load(f)
    if SECRETS.exists():
        with SECRETS.open("rb") as f:
            cfg.setdefault("notify", {}).update(tomllib.load(f).get("notify") or {})
    return cfg


def load_state(cfg):
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    d = cfg["defaults"]
    # ntfy `since` starts now: answers older than the first run do not count.
    state.setdefault("_notify", {"since": str(int(time.time()))})
    state.setdefault("_window", {"active": False})
    # Answers typed in the main topic: likewise, only those arriving from now on count.
    state["_notify"].setdefault("since_typed", str(int(time.time())))
    for p in cfg["project"]:
        s = state.setdefault(p["name"], {})
        s.setdefault("status", "active")  # active | idle | blocked | waiting
        s.setdefault("session", None)
        s.setdefault("interrupted", False)
        s.setdefault("errors", 0)
        s.setdefault("early_ends", 0)
        s.setdefault("blocked_until", 0)
        s.setdefault("cost", {"primary": d["initial_cost_primary"], "secondary": d["initial_cost_secondary"]})
        s.setdefault("dirty_logged", False)
        s.setdefault("unit_start_limits", None)
        s.setdefault("notes_sent", [])
        s.setdefault("unit_id", None)
        s.setdefault("run", None)
    return state


def save_state(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE)


# --- notifications and decisions (ntfy) -------------------------------------------------------


def ntfy_server(cfg):
    return (cfg.get("notify") or {}).get("server", "https://ntfy.sh").rstrip("/")


def ntfy(cfg, title, message, priority=3, tags=(), actions=None):
    """Publishes to the ntfy topic. A network failure is logged and never brings the scheduler down.

    Phone texts stay in Brazilian Portuguese, the owner's language, until there is a language setting.
    """
    topic = (cfg.get("notify") or {}).get("topic")
    if not topic:
        return
    body = {"topic": topic, "title": title, "message": message, "priority": priority, "tags": list(tags)}
    if actions:
        body["actions"] = actions
    req = urllib.request.Request(ntfy_server(cfg), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15).close()
    except OSError as e:
        log(f"ntfy falhou: {e}")


def reply_topic(cfg):
    return cfg["notify"]["topic"] + "-reply"


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def decision_files(project):
    folder = Path(project["path"]) / DECISIONS
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def valid_decision(d, path):
    options = d.get("options")
    return (isinstance(options, list) and 1 <= len(options) <= 3
            and all(isinstance(o, dict) and re.fullmatch(r"[\w-]+", str(o.get("id", ""))) and o.get("label")
                    for o in options)
            and d.get("default") in {o["id"] for o in options}
            and d.get("id") == path.stem and re.fullmatch(r"[\w-]+", path.stem) and d.get("question"))


def option_label(d, option):
    return next((o["label"] for o in d.get("options") or [] if o.get("id") == option), option)


def answer_decision(project, path, d, option, by):
    d.update(answer=option, answered_by=by, answered_at=time.strftime("%FT%T%z"))
    write_json(path, d)
    log(f"{project['name']}: decision {d.get('id')} = {option} ({by})")
    return option_label(d, option)


def fetch_messages(cfg, topic, since, label):
    """Messages of a topic since `since`; None if the network fails."""
    url = f"{ntfy_server(cfg)}/{topic}/json?poll=1&since={urllib.parse.quote(since)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            lines = r.read().decode(errors="replace").splitlines()
    except OSError as e:
        log(f"ntfy ({label}) falhou: {e}")
        return None
    out = []
    for line in lines:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("event") == "message":
            out.append(msg)
    return out


def owner_answer(cfg, project, path, d, option):
    label = answer_decision(project, path, d, option, "owner")
    ntfy(cfg, f"{project['name']}: anotado", f"{d['question']}\n→ {label}", priority=2, tags=["white_check_mark"])


def open_decisions(project):
    """Decisions already sent to the phone and still unanswered: the only ones that accept an answer."""
    out = []
    for path in decision_files(project):
        d = read_json(path)
        if d and "answer" not in d and d.get("notified_at") and valid_decision(d, path):
            out.append((path, d))
    return out


# Option letter at the start, alone or followed by punctuation: "B", "b)", "B, manter até…".
# The project may come first: "myapp B", "myapp: b". "a primeira" does not match (letter followed by a word).
TYPED_REPLY = re.compile(r"(?:([\w-]+)[\s/:]+)?([a-z])\s*(?:$|[,.;:)\-–—])", re.I)


def note_targets(projects, text):
    """Projetos citados pelo nome na frase; nenhum citado = todos."""
    named = [p for name, p in projects.items() if re.search(rf"\b{re.escape(name)}\b", text, re.I)]
    return named or list(projects.values())


def leave_note(cfg, targets, text):
    for p in targets:
        coord.add_note(p["name"], text)
    names = ", ".join(p["name"] for p in targets)
    ntfy(cfg, "autopilot: mensagem anotada", f"Para {names}, na próxima unidade:\n{text[:200]}",
         priority=2, tags=["memo"])
    log(f"owner message noted for {names}")


def typed_reply(cfg, projects, text):
    """A message typed in the app: a letter at the start answers an open decision; anything else goes to the agents.

    Unprotected by the owner's choice: whoever knows the topic can also send messages. The protocol's
    "Never" rules still stand above any message.
    """
    if not text or text.lower() == "triggered":  # "triggered" is the text of an empty ntfy message
        return
    m = TYPED_REPLY.match(text)
    if m and m.group(1) and m.group(1).lower() not in projects:
        m = None
    if not m:
        leave_note(cfg, note_targets(projects, text), text)
        return
    comment = text[m.end():].strip(" ,.;:-–—")
    option = m.group(2).lower()
    scope = [projects[m.group(1).lower()]] if m.group(1) else list(projects.values())
    candidates = [(p, path, d) for p in scope for path, d in open_decisions(p)]
    fits = [(p, path, d) for p, path, d in candidates if option in {o["id"] for o in d["options"]}]
    if comment and not fits:
        # "E aí, tudo certo?" starts like a letter but answers nothing: it is a message.
        leave_note(cfg, note_targets(projects, text), text)
        return
    if not candidates:
        ntfy(cfg, "autopilot: nenhuma pergunta aberta", f"Não há decisão esperando resposta. Ignorei: {text[:80]}",
             priority=2, tags=["grey_question"])
        return
    if len(candidates) > 1:
        names = sorted({p["name"] for p, _, _ in candidates})
        hint = f"ponha o projeto antes da letra, por exemplo: {names[0]} {option.upper()}" if len(names) > 1 \
            else "use os botões da notificação"
        ntfy(cfg, "autopilot: qual pergunta?", f"Há {len(candidates)} perguntas abertas; {hint}.",
             priority=3, tags=["grey_question"])
        return
    project, path, d = candidates[0]
    ids = {o["id"] for o in d["options"]}
    if option not in ids:
        ntfy(cfg, f"{project['name']}: opção inexistente",
             f"{d['question']}\nAs opções são: {', '.join(sorted(i.upper() for i in ids))}.",
             priority=3, tags=["grey_question"])
        return
    owner_answer(cfg, project, path, d, option)
    if comment:
        coord.add_note(project["name"], f"Comment on the answer to decision {d['id']} (option {option.upper()}): {comment}")


def poll_replies(cfg, notify):
    """Reads the answers: the buttons (the -reply topic, project/decision/option format) and those typed in the app.

    `notify` is the shared ntfy state (since, since_typed), the same for both instances.
    """
    if not (cfg.get("notify") or {}).get("topic"):
        return
    now = str(int(time.time()))
    notify.setdefault("since", now)
    notify.setdefault("since_typed", now)
    projects = {p["name"]: p for p in cfg["project"]}
    for msg in fetch_messages(cfg, reply_topic(cfg), notify["since"], "respostas") or []:
        notify["since"] = msg["id"]
        m = re.fullmatch(r"([\w-]+)/([\w-]+)/([\w-]+)", (msg.get("message") or "").strip())
        if not m or m.group(1) not in projects:
            continue
        project = projects[m.group(1)]
        path = Path(project["path"]) / DECISIONS / f"{m.group(2)}.json"
        d = read_json(path)
        # An answer to an unknown or already answered decision, or with an unlisted option, is ignored.
        if not d or "answer" in d or not valid_decision(d, path) or m.group(3) not in {o["id"] for o in d["options"]}:
            continue
        owner_answer(cfg, project, path, d, m.group(3))
    for msg in fetch_messages(cfg, cfg["notify"]["topic"], notify["since_typed"], "respostas digitadas") or []:
        notify["since_typed"] = msg["id"]
        # Everything the scheduler publishes has a title; a message without one is someone typing in the app.
        if msg.get("title"):
            continue
        typed_reply(cfg, projects, (msg.get("message") or "").strip())


def dispatch_decisions(cfg, notify):
    """Reads answers, sends new decisions and applies the default once the deadline passes."""
    hours = (cfg.get("notify") or {}).get("decision_timeout_hours", 4)
    poll_replies(cfg, notify)
    now = time.time()
    for p in cfg["project"]:
        for path in decision_files(p):
            d = read_json(path)
            if d is None or "answer" in d:
                continue
            if not valid_decision(d, path):
                # Wrong format: it cannot be asked; the next unit sees it and fixes it.
                d.update(answer=None, answered_by="invalid-format", answered_at=time.strftime("%FT%T%z"))
                write_json(path, d)
                log(f"{p['name']}: decision with an invalid format in {path}")
            elif not d.get("notified_at"):
                d.update(notified_at=now, deadline=now + hours * 3600)
                write_json(path, d)
                lines = [f"{o['id'].upper()}) {o['label']}" for o in d["options"]]
                actions = [{"action": "http", "label": o["label"][:40], "url": f"{ntfy_server(cfg)}/{reply_topic(cfg)}",
                            "method": "POST", "body": f"{p['name']}/{d['id']}/{o['id']}", "clear": True}
                           for o in d["options"]]
                message = "\n".join([d["question"], "", d.get("context", ""), "", *lines, "",
                                     f"Responda pelos botões ou digitando a letra (ex.: {d['options'][0]['id'].upper()}).",
                                     f"Sem resposta em {hours} h: {option_label(d, d['default'])}"])
                ntfy(cfg, f"{p['name']}: decisão", message.strip(), priority=4, tags=["question"], actions=actions)
                log(f"{p['name']}: decision {d['id']} sent; deadline {hours} h")
            elif now > d.get("deadline", 0):
                label = answer_decision(p, path, d, d["default"], "timeout")
                ntfy(cfg, f"{p['name']}: sem resposta", f"{d['question']}\n→ {label} (padrão)", tags=["hourglass"])


def process_decisions(cfg, state):
    """Talks to ntfy (one instance at a time) and releases the projects whose decisions were resolved."""
    lock = coord.try_lock("decisions")
    if lock:
        try:
            # The first time, the read position comes from this instance's state.json.
            legacy = {k: state["_notify"][k] for k in ("since", "since_typed") if k in state["_notify"]}
            notify = coord.load_notify(legacy)
            dispatch_decisions(cfg, notify)
            coord.save_notify(notify)
        finally:
            coord.unlock(lock)
    for p in cfg["project"]:
        s = state[p["name"]]
        pending = any(d and "answer" not in d for d in map(read_json, decision_files(p)))
        if s["status"] == "waiting" and not pending:
            s["status"] = "active"
            log(f"{p['name']}: decisions resolved; project released")


def next_deadline(project):
    deadlines = [d.get("deadline", 0) for d in map(read_json, decision_files(project))
                 if d and "answer" not in d and d.get("deadline")]
    return min(deadlines) if deadlines else time.time() + 300


def overrides_mtime():
    try:
        return (HOME / overrides.FILE).stat().st_mtime_ns
    except OSError:
        return 0


def nap(cfg, state, secs):
    """Sleeps in 5-second steps; wakes early when the overrides file changes or a waiting project is released.

    Answers are read once a minute, and right away when the overrides change.
    """
    end = time.time() + secs
    seen = overrides_mtime()
    last = time.time()
    while (left := end - time.time()) > 0:
        time.sleep(min(5, left))
        changed = overrides_mtime() != seen
        if not changed and time.time() - last < 60:
            continue
        last = time.time()
        before = {p["name"]: state[p["name"]]["status"] for p in cfg["project"]}
        process_decisions(cfg, state)
        save_state(state)
        if changed or any(state[name]["status"] != status for name, status in before.items()):
            return


# --- execution -----------------------------------------------------------------------------


def git(path, *args):
    return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True).stdout.strip()


def is_dirty(path):
    # The autopilot's own files do not count: the scheduler writes the answer into the decision
    # file, and in a project that versions that folder the tree would stay dirty forever.
    return bool(git(path, "status", "--porcelain", "--", ".", f":(exclude){UNIT_FILE.parent}"))


def compose(path, *args):
    if not (Path(path) / "docker-compose.yml").exists() and not (Path(path) / "compose.yaml").exists():
        return True
    r = subprocess.run(["docker", "compose", *args], cwd=path, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"docker compose {' '.join(args)} falhou em {path}: {r.stderr.strip()[-300:]}")
    return r.returncode == 0


DOCKER_DESKTOP = Path("/mnt/c/Program Files/Docker/Docker/Docker Desktop.exe")


def docker_up():
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def ensure_docker(cfg):
    """Makes sure the Docker daemon is up; opens Windows Docker Desktop if it is closed."""
    if docker_up():
        return True
    if DOCKER_DESKTOP.exists():
        log("Docker is down; opening Docker Desktop")
        subprocess.Popen([str(DOCKER_DESKTOP)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(18):
            time.sleep(10)
            if docker_up():
                log("Docker no ar")
                return True
    return False


def prepare_docker(cfg, project):
    """Brings the project's stack up. Returns "ok", "docker-down", "busy" or "compose-failed"."""
    if not ensure_docker(cfg):
        log("Docker did not respond; no unit runs until it is back")
        return "docker-down"
    group = project.get("docker_group")
    if group:
        for other in cfg["project"]:
            if other is not project and other.get("docker_group") == group:
                running = subprocess.run(["docker", "compose", "ps", "-q"], cwd=other["path"],
                                         capture_output=True, text=True).stdout.strip()
                if not running:
                    continue
                if is_dirty(other["path"]):
                    # Someone is working on the other project: stopping its stack would break that work.
                    log(f"{project['name']}: portas ocupadas por {other['name']}, que tem trabalho em andamento; esperando")
                    return "busy"
                log(f"stopping the {other['name']} stack (same ports as {project['name']})")
                compose(other["path"], "stop")
    # --wait waits for the healthchecks: the unit does not start while the database is still coming up.
    if not compose(project["path"], "up", "-d", "--wait", "--wait-timeout", "180"):
        return "compose-failed"
    return "ok"


def owner_messages(messages):
    lines = [f"- [{time.strftime('%Y-%m-%d %H:%M', time.localtime(n['at']))}] {n['text']}" for n in messages]
    return ("Owner messages (sent from the phone, oldest first; they may be in Portuguese):\n"
            + "\n".join(lines) + "\n"
            "Follow them in this unit when they concern this project and ignore those that do not. Record "
            "a lasting instruction in the decisions file so later units keep it. They never override step 7.")


CUT = "cut"  # "exit code" of a unit cut at the end of the window
STOPPED = "stopped"  # "exit code" of a unit interrupted from the panel (SIGUSR1)
CURRENT = None  # process of the running unit, so Ctrl+C can end its group
CURRENT_UNIT = None  # (engine, project, s) of the running unit, for the history line on stop
STOP_REQUESTED = False  # set by request_interrupt; read by run_unit once the process ends
EXIT_REQUESTED = False  # set by the SIGINT/SIGTERM handler while a unit runs; run_unit then exits
STOP_AT = 0.0  # when the SIGTERM was sent to the group; wait_until escalates to SIGKILL after 30 s


def stop_group(proc):
    """SIGTERM to the process group; SIGKILL if it does not exit within 30 s."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    except ProcessLookupError:
        pass


def _term_group(proc):
    """SIGTERM to the group without blocking: safe inside a signal handler (never waits on the Popen)."""
    global STOP_AT
    STOP_AT = time.time()
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def request_interrupt(*_):
    """SIGUSR1 handler: notes the request and sends SIGTERM to the unit's group. No live unit: nothing.

    It never waits: the main thread is blocked in `proc.wait` holding the Popen lock, and `wait_until`
    does the rest (SIGKILL after 30 s).
    """
    global STOP_REQUESTED
    proc = CURRENT
    if proc is None or proc.poll() is not None:
        return
    STOP_REQUESTED = True
    _term_group(proc)


def make_exit_handler(state):
    """SIGINT/SIGTERM handler. With a unit running it only asks for the exit; run_unit does it."""
    def on_signal(*_):
        global EXIT_REQUESTED
        proc = CURRENT
        if proc is not None:
            EXIT_REQUESTED = True
            _term_group(proc)
            return
        save_state(state)
        log("interrupted; run again to resume")
        sys.exit(130)
    return on_signal


def record_interrupted_run():
    """One `stopped` history line for the unit running now (Ctrl+C / SIGTERM). No-op without a unit."""
    unit = CURRENT_UNIT
    if unit is not None:
        engine, project, s = unit
        record_run(engine, project, s, "stopped")


def wait_until(proc, deadline):
    """Espera o processo em fatias de 5 s; passado `deadline` (timestamp), encerra o grupo e devolve CUT.

    Nunca espera sem timeout: os handlers de sinal só mandam SIGTERM, e aqui, 30 s depois, vem o SIGKILL.
    """
    while True:
        left = None if deadline is None else deadline - time.time()
        if left is not None and left <= 0:
            stop_group(proc)
            return CUT
        try:
            return proc.wait(timeout=5 if left is None else min(left, 5))
        except subprocess.TimeoutExpired:
            if (STOP_REQUESTED or EXIT_REQUESTED) and time.time() - STOP_AT >= 30:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def set_phase(state, phase, project=None, until=None):
    """What the loop is doing, for the panel: running, sleeping or outside_window."""
    state["_loop"] = {"phase": phase, "project": project, "until": until}


def idle_phase(win, now):
    return "outside_window" if win and not win.contains(now) else "sleeping"


def run_unit(cfg, engine, state, project, deadline=None):
    global CURRENT, CURRENT_UNIT, STOP_REQUESTED, EXIT_REQUESTED
    s = state[project["name"]]
    path = project["path"]
    resume = engine.resumable(s)
    # replace, not format: the prompts have JSON examples with braces.
    prompt = (PROMPTS / ("resume.md" if resume else "unit.md")).read_text().replace("{project}", project["name"])
    notes = PROMPTS / f"engine-{engine.name}.md"
    if notes.exists():
        prompt += "\n\n" + notes.read_text()
    messages = coord.pending_notes(project["name"])
    if messages:
        prompt += "\n\n" + owner_messages(messages)
    # Delivered only when the unit closes; on a resume they are sent again.
    s["notes_sent"] = [n["id"] for n in messages]
    unit_file = Path(path) / UNIT_FILE
    if not resume:
        unit_file.unlink(missing_ok=True)

    RUNS.mkdir(exist_ok=True)
    base = RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-{project['name']}"
    stem, n = base, 2
    # Two runs in the same second (or a clock that stepped back) must not share a name: the name is
    # the run's file and, on a new unit, its unit_id.
    while Path(f"{stem}.jsonl").exists():
        stem, n = Path(f"{base}-{n}"), n + 1
    out, err = f"{stem}.jsonl", f"{stem}.err"
    if not s.get("unit_id"):
        s["unit_id"] = stem.name  # the first run names the unit; resumes and the baton keep it
    s["run"] = {"id": stem.name, "started": time.time()}
    engine.begin(s, resume)
    cmd, env = engine.command(state, project, s, resume, stem)
    log(f"{project['name']}: {'resuming session ' + s['session'] if resume else 'new unit'}")
    set_phase(state, "running", project["name"])
    s["interrupted"] = True  # back to False only when the unit really ends
    save_state(state)  # if the loop dies now, the unit stays marked as open
    with open(out, "w") as fo, open(err, "w") as fe:
        # Own process group: the cut and Ctrl+C also end the processes the CLI started.
        STOP_REQUESTED = EXIT_REQUESTED = False
        try:
            CURRENT_UNIT = (engine, project, s)
            CURRENT = subprocess.Popen(cmd, cwd=path, stdin=subprocess.PIPE, stdout=fo, stderr=fe, text=True,
                                       env=env, start_new_session=True)
            try:
                CURRENT.stdin.write(prompt)
                CURRENT.stdin.close()
            except BrokenPipeError:
                pass
            code = wait_until(CURRENT, deadline)
        finally:
            CURRENT = None
            if not EXIT_REQUESTED:
                CURRENT_UNIT = None  # a failed Popen must not leave it behind
            exit_now, stopped = EXIT_REQUESTED, STOP_REQUESTED
            STOP_REQUESTED = EXIT_REQUESTED = False
    if exit_now:
        # Ctrl+C / SIGTERM during the unit: one history line, then out, from the main thread.
        record_interrupted_run()
        CURRENT_UNIT = None
        save_state(state)
        log("interrupted; run again to resume")
        sys.exit(130)
    CURRENT_UNIT = None
    if stopped:
        code = STOPPED
    engine.after_run(state, s, out, resume)
    for old in sorted(RUNS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[KEEP_RUNS * 3:]:
        old.unlink(missing_ok=True)
    return out, err, code, unit_file


def record_run(engine, project, s, outcome, result=None, cost=None):
    """One history line per finished run. Summary and cost only on the run that closes the unit."""
    run = s.get("run") or {}
    entry = {"unit_id": s.get("unit_id") or run.get("id"), "run": run.get("id"), "project": project["name"],
             "engine": engine.name, "started_at": history.iso(run.get("started") or time.time()),
             "ended_at": history.iso(time.time()), "outcome": outcome, "closed": result is not None}
    if result is not None:
        entry.update(unit=result.get("unit"), summary=result.get("summary"),
                     milestone=result.get("milestone"), cost=cost)
    try:
        history.append(HISTORY, entry)
    except OSError as e:
        log(f"history not written: {e}")
    s["run"] = None


def after_unit(cfg, engine, state, project, out, err, code, unit_file):
    """Updates the project state and returns how many seconds to sleep before the next unit."""
    d = cfg["defaults"]
    s = state[project["name"]]
    if code == STOPPED:
        # Not closed: no cost, the session and the claim stay, `interrupted` stays True.
        record_run(engine, project, s, "stopped")
        log(f"{project['name']}: unit interrupted from the panel; it resumes when the project is resumed")
        return 5
    if code == CUT:
        record_run(engine, project, s, "cut")
        pass_baton(engine, project["name"], s)
        log(f"{project['name']}: unit cut at the end of the window; baton passed")
        return 5
    status, reset, hard = engine.classify(out, err, code)

    if status == "usage_limit":
        record_run(engine, project, s, "usage_limit")
        s["errors"] = 0
        wait = (reset - time.time() + 120) if reset else (1800 if hard else 300)
        wait = max(wait, 300)
        log(f"{project['name']}: usage limit; the unit continues after the reset "
            f"({time.strftime('%F %T', time.localtime(time.time() + wait))})")
        return wait

    if status == "error":
        record_run(engine, project, s, "error")
        s["errors"] += 1
        log(f"{project['name']}: erro (exit={code}, tentativa {s['errors']}; ver {err})")
        if s["errors"] % 3 == 0:
            # A session that does not resume: the next unit restarts from the ledger (recovery step).
            log(f"{project['name']}: dropping the session after repeated errors")
            s["session"] = None
            ntfy(cfg, f"{project['name']}: erros seguidos",
                 f"{s['errors']} execuções com erro. Último log: {err}", priority=4, tags=["warning"])
        return min(60 * 2 ** min(s["errors"], 5), 1800)

    s["errors"] = 0
    try:
        result = json.loads(unit_file.read_text())
    except (OSError, ValueError):
        result = None
    if not result:
        record_run(engine, project, s, "unfinished")
        # The response ended without closing the unit: resume the same session, up to twice.
        s["early_ends"] += 1
        if s["early_ends"] >= 3:
            log(f"{project['name']}: the unit does not close; restarting from the ledger in a new session")
            s["session"], s["early_ends"] = None, 0
        else:
            log(f"{project['name']}: response ended without last-unit.json; resuming")
        return 5

    # Measure the whole session only when the unit closes, so a resume is not counted twice.
    cost = engine.unit_cost(state, s)
    if cost:
        for key in ("primary", "secondary"):
            s["cost"][key] = round((1 - EMA_ALPHA) * s["cost"][key] + EMA_ALPHA * cost[key], 3)
    record_run(engine, project, s, result.get("status", "worked"), result=result, cost=cost)
    engine.close_unit(s)
    s.update(session=None, interrupted=False, early_ends=0, unit_id=None)
    coord.release(project["name"], engine.name)
    if s["notes_sent"]:
        coord.mark_delivered(project["name"], s["notes_sent"])
        ntfy(cfg, f"{project['name']}: mensagem entregue ao {engine.label}",
             f"{len(s['notes_sent'])} mensagem(ns) lida(s) nesta unidade: {result.get('unit', '?')}",
             priority=2, tags=["envelope"])
        s["notes_sent"] = []
    unit_status = result.get("status", "worked")
    summary = result.get("summary", "")
    log(f"{project['name']}: {unit_status} — {result.get('unit', '?')}")
    if not backlog.valid_backlog(read_json(Path(project["path"]) / backlog.FILE)):
        log(f"{project['name']}: backlog.json missing or invalid; the panel has no progress for it")
    if unit_status == "idle":
        s["status"] = "idle"
        ntfy(cfg, f"{project['name']}: sem trabalho", summary or "Backlog concluído.", tags=["checkered_flag"])
    elif unit_status == "blocked":
        s["status"] = "blocked"
        s["blocked_until"] = time.time() + d["blocked_cooldown_hours"] * 3600
        ntfy(cfg, f"{project['name']}: bloqueado", summary, priority=4, tags=["no_entry"])
    elif unit_status == "waiting":
        s["status"] = "waiting"
    if result.get("milestone"):
        ntfy(cfg, f"{project['name']}: {result['milestone']}", summary, priority=2, tags=["tada"])
    return 5


def pick(cfg, engine, state, limits, next_project=None):
    """Picks the next project and takes its group lock.

    Returns (project, lock, None) or (None, None, seconds until it is worth checking again).
    """
    d = cfg["defaults"]
    pri_used, pri_reset = window(limits, "primary")
    sec_used, sec_reset = window(limits, "secondary")
    now = time.time()
    waits = []

    # This engine's interrupted unit and any engine's baton go first: nothing is left half-done.
    for p in cfg["project"]:
        name = p["name"]
        if p.get("paused"):
            continue
        if not (state[name]["interrupted"] or coord.is_handoff(name)) or coord.held_by_other(name, engine.name):
            continue
        lock = coord.try_lock(coord.group_of(p))
        if lock:
            return p, lock, None
        waits.append(60)

    queue = list(cfg["project"])
    first = [p for p in queue if p["name"] == next_project and not p.get("paused")]
    queue = first + [p for p in queue if p not in first]
    for p in queue:
        name = p["name"]
        s = state[name]
        if p.get("paused"):
            continue
        if s["interrupted"] or coord.is_handoff(name):
            continue  # already handled above (the group was busy)
        if s["status"] == "idle":
            continue
        if s["status"] == "waiting":
            waits.append(max(60, next_deadline(p) - now))
            continue
        if s["status"] == "blocked":
            if s["blocked_until"] > now:
                waits.append(s["blocked_until"] - now)
                continue
            s["status"] = "active"
        if coord.held_by_other(name, engine.name):
            waits.append(600)
            continue
        if is_dirty(p["path"]):
            if not s["dirty_logged"]:
                log(f"{name}: tree has changes that are not the autopilot's; skipping until it is clean")
                s["dirty_logged"] = True
            waits.append(600)
            continue
        s["dirty_logged"] = False
        if sec_used + d["safety_factor"] * s["cost"]["secondary"] > p["weekly_cap"]:
            if sec_reset:
                waits.append(sec_reset - now + 120)
            continue
        # 5-hour window per project: if the first in line does not fit, a cheaper one can still run.
        if pri_used + d["safety_factor"] * s["cost"]["primary"] > d["primary_cap"]:
            waits.append((pri_reset - now + 120) if pri_reset else 1800)
            continue
        lock = coord.try_lock(coord.group_of(p))
        if not lock:
            waits.append(60)  # the other engine runs a project of the same Docker group
            continue
        return p, lock, None
    return None, None, (min(waits) if waits else 3600)


def pass_baton(engine, name, s):
    """End of the window: the work stays in the repository and the ledger, and any engine can resume it."""
    engine.close_unit(s)
    coord.hand_off(name, engine.name, s.get("unit_id"))
    s.update(interrupted=False, session=None, early_ends=0, errors=0, unit_id=None)


def hand_off_open_units(cfg, engine, state):
    """Outside the window no unit of this engine stays open: pass the baton of the interrupted ones."""
    for p in cfg["project"]:
        s = state[p["name"]]
        if s["interrupted"] and not coord.held_by_other(p["name"], engine.name):
            pass_baton(engine, p["name"], s)
            log(f"{p['name']}: window closed with the unit interrupted; baton passed")


def window_notice(cfg, engine, state, active):
    """Announces the start and end of the window, with the weekly quota usage."""
    # On closing the last reading applies: outside the window the quota is not queried.
    used, _ = window(engine.limits(state, max_age=0 if active else float("inf")), "secondary")
    if active:
        cap = max(p["weekly_cap"] for p in cfg["project"])
        log(f"window open; weekly quota at {used:.0f}%")
        ntfy(cfg, f"{engine.label}: começou o fim de semana", f"Cota semanal em {used:.0f}%; teto {cap}%.",
             priority=2, tags=["arrow_forward"])
        return
    handed = [name for name, c in coord.claims().items()
              if c and c.get("engine") == coord.ANY and c.get("handoff_from") == engine.name]
    tail = f"Ficaram com o bastão: {', '.join(handed)}." if handed else "Nenhuma unidade ficou pela metade."
    log(f"window closed; weekly quota at {used:.0f}%")
    ntfy(cfg, f"{engine.label}: fim de semana encerrado", f"Cota semanal em {used:.0f}%. {tail}",
         priority=2, tags=["stop_button"])


def run_once(cfg, engine, state, win=None, now=None):
    """One turn of the loop. Returns how many seconds to sleep, or None when there is no more work."""
    process_decisions(cfg, state)
    started = time.time()
    now = now or datetime.datetime.now()
    ov = overrides.read(HOME)
    resets = state.setdefault("_resets", {})
    for name, ts in overrides.resets(ov).items():
        if name in state and name in {p["name"] for p in cfg["project"]} and ts > resets.get(name, 0):
            state[name].update(status="active", blocked_until=0, errors=0)
            resets[name] = ts
            log(f"{name}: reset from the panel")
    cfg = overrides.apply(cfg, engine.name, ov)
    if win:
        active = win.contains(now)
        if not active:
            hand_off_open_units(cfg, engine, state)
        if active != state["_window"]["active"]:
            state["_window"]["active"] = active
            window_notice(cfg, engine, state, active)
        if not win.can_start(now):
            # Outside the window the quota is not even queried: it belongs to the owner on those days.
            until = win.end_of(now) if active else win.next_start(now)
            save_state(state)
            return max(60, (until - now).total_seconds())
    limits = engine.limits(state)
    if engine.needs_limits and not limits:
        log("no quota reading; no new unit until it can be read")
        save_state(state)
        return 600
    project, lock, wait = pick(
        cfg, engine, state, limits, next_project=ov.get("next") if isinstance(ov.get("next"), str) else None)
    save_state(state)
    if project is None:
        if all(state[p["name"]]["status"] == "idle" for p in cfg["project"]):
            log("all projects idle; done")
            ntfy(cfg, f"{engine.label}: fim", "Todos os projetos terminaram o backlog.", tags=["checkered_flag"])
            return None
        # Not logged: the log shows only work. The reason for the wait shows in `check`.
        return max(wait, 60)
    try:
        wait = run_project(cfg, engine, state, project, limits, win)
    finally:
        coord.unlock(lock)
    if win:
        # A long wait (usage limit, error) cannot go past the end of the window: at closing time
        # the loop has to be awake to pass the baton.
        left = (win.end_of(now) - now).total_seconds() - (time.time() - started)
        wait = min(wait, max(5, left + 5))
    return wait


def run_project(cfg, engine, state, project, limits, win):
    """Runs a unit on the chosen project (the caller already holds the group lock)."""
    name = project["name"]
    s = state[name]
    current = coord.read_claim(name)
    handoff = bool(current) and current.get("engine") == coord.ANY
    if not coord.claim(name, engine.name):
        log(f"{name}: claimed by the other engine; skipping")
        return 60
    if not s["interrupted"] and not handoff:
        overrides.take_next(HOME, name)
    if handoff:
        log(f"{name}: taking the baton left by {current.get('handoff_from')}")
        engine.close_unit(s)
        s.update(interrupted=True, session=None, early_ends=0, unit_id=current.get("unit_id"))

    docker = prepare_docker(cfg, project)
    if docker != "ok":
        if not s["interrupted"]:
            coord.release(name, engine.name)  # nothing started: the project is free for the other engine
        if docker != "busy" and not state["_notify"].get("docker_alerted"):
            # Without the containers the unit would only burn quota; warn once and retry in 10 min.
            ntfy(cfg, f"{engine.label}: Docker com problema",
                 f"Não consegui subir os containers de {name} ({docker}). Veja o log.", priority=4, tags=["warning"])
            state["_notify"]["docker_alerted"] = True
        return 600
    state["_notify"]["docker_alerted"] = False
    deadline = win.end_of(datetime.datetime.now()).timestamp() if win else None
    out, err, code, unit_file = run_unit(cfg, engine, state, project, deadline)
    wait = after_unit(cfg, engine, state, project, out, err, code, unit_file)
    save_state(state)
    return wait


def cmd_run(args):
    cfg = load_config()
    engine = engines.make_engine(cfg)
    instance = instance_lock(engine.name)  # kept open while the loop runs
    if instance is None:
        sys.exit(f"the {engine.label} loop is already running (pid {running_pid(engine.name)})")
    state = load_state(cfg)
    win = schedule.from_config(cfg)

    on_signal = make_exit_handler(state)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGUSR1, request_interrupt)
    log(f"autopilot started ({engine.describe()}{', window ' + win.describe() if win else ''})")
    open_units = {p["name"]: state[p["name"]]["interrupted"] for p in cfg["project"]}
    for name in coord.reconcile(engine.name, open_units):
        log(f"{name}: unit open here, but the project is claimed by the other engine; leaving it aside")
        ntfy(cfg, f"{engine.label}: projeto em conflito",
             f"{name} tem unidade aberta nos dois autopilots. Veja os logs.", priority=4, tags=["warning"])
    save_state(state)
    while True:
        wait = run_once(cfg, engine, state, win)
        if wait is None:
            return
        set_phase(state, idle_phase(win, datetime.datetime.now()), until=time.time() + wait)
        save_state(state)
        nap(cfg, state, wait)


def status_lines(cfg, engine, state, limits, win, now):
    out = [f"engine {engine.label}"]
    if win:
        if win.contains(now):
            out.append(f"window {win.describe()}: inside, closes {schedule.label(win.end_of(now))}")
        else:
            out.append(f"window {win.describe()}: outside, opens {schedule.label(win.next_start(now))}")
    if engine.needs_limits and not limits:
        out.append("quota: no reading")
    for key, label in (("primary", "5 h"), ("secondary", "weekly")):
        used, reset = window(limits, key)
        when = time.strftime("%F %T", time.localtime(reset)) if reset else "-"
        out.append(f"window {label:8} {used:5.1f}%   reset {when}")
    if hasattr(engine, "fable_available") and not engine.fable_available(state):
        until = state["_engine"]["fable_exhausted_until"]
        out.append(f"Fable spared until {time.strftime('%F %T', time.localtime(until))}; planning on Opus")
    out.append("")
    claims = coord.claims()
    for p in cfg["project"]:
        s = state[p["name"]]
        flags = []
        if s["interrupted"]:
            flags.append("unit interrupted")
        if s["session"]:
            flags.append(f"session {s['session']}")
        c = claims.get(p["name"])
        if c and c.get("engine") == coord.ANY:
            flags.append(f"baton from {c.get('handoff_from')}")
        elif c:
            flags.append(f"claimed: {c.get('engine')}")
        if is_dirty(p["path"]):
            flags.append("dirty tree")
        out.append(f"{p['name']:10} {s['status']:8} cap {p['weekly_cap']:>3}%  "
                   f"cost/unit {s['cost']['primary']:.1f}% (5 h) {s['cost']['secondary']:.2f}% (week)  "
                   f"{', '.join(flags)}")
    pending = sum(1 for p in cfg["project"] for d in map(read_json, decision_files(p)) if d and "answer" not in d)
    if pending:
        out.append(f"\n{pending} pending decision(s): autopilot.py decisions")
    return out


# --- starting and stopping both loops ----------------------------------------------------------

TMUX_SESSION = "autopilot"
# (tmux window and engine name, config): the Codex one is the default.
LOOPS = (("codex", None), ("claude", HOME / "claude.toml"))


def instance_lock(name):
    """One loop per engine: the flock lasts while the loop runs and the file keeps the holder's pid."""
    handle = coord.try_lock(f"instance-{name}")
    if handle is not None:
        handle.truncate(0)
        handle.write(str(os.getpid()))
        handle.flush()
    return handle


def running_pid(name):
    """The engine loop's pid if it is running; None if the lock is free."""
    handle = coord.try_lock(f"instance-{name}")
    if handle is not None:
        coord.unlock(handle)
        return None
    try:
        return int((coord.shared_dir() / f"instance-{name}.lock").read_text().strip())
    except (OSError, ValueError):
        return 0  # running, but the pid is not written yet


def is_loop(argv, config):
    """Whether the command line is a loop (`autopilot.py [--config X] run`) of this config."""
    if len(argv) < 3 or not argv[1].endswith("autopilot.py") or argv[-1] != "run":
        return False
    given = argv[argv.index("--config") + 1] if "--config" in argv[:-1] else None
    if config is None:
        return given is None
    return given is not None and Path(given).name == Path(config).name


def loop_pids(config):
    """Loops of this config already running, including those started by hand the old way."""
    pids = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            argv = (proc / "cmdline").read_bytes().decode(errors="replace").split("\0")[:-1]
        except OSError:
            continue
        if int(proc.name) != os.getpid() and is_loop(argv, config):
            pids.append(int(proc.name))
    return pids


def tmux(*args):
    r = subprocess.run(["tmux", *args], capture_output=True, text=True)
    return r.returncode, r.stdout


def loop_command(config):
    script = shlex.quote(str(Path(__file__).resolve()))
    return f"python3 {script}" + (f" --config {shlex.quote(str(config))}" if config else "") + " run"


def log_file(name, config):
    return HOME / ("autopilot.log" if config is None else f"autopilot-{name}.log")


def open_logs_window(has_session, windows):
    """The "logs" window: both logs live, one above the other. It is the one `tmux attach` shows."""
    target = f"{TMUX_SESSION}:logs"
    if "logs" not in windows:
        (top_name, top_cfg), (bottom_name, bottom_cfg) = LOOPS
        top = f"tail -n 40 -F {shlex.quote(str(log_file(top_name, top_cfg)))}"
        bottom = f"tail -n 40 -F {shlex.quote(str(log_file(bottom_name, bottom_cfg)))}"
        if has_session:
            tmux("new-window", "-d", "-t", TMUX_SESSION, "-n", "logs", top)
        else:
            tmux("new-session", "-d", "-s", TMUX_SESSION, "-n", "logs", top)
        tmux("split-window", "-v", "-d", "-t", target, bottom)
        tmux("set-option", "-w", "-t", target, "pane-border-status", "top")
        tmux("set-option", "-w", "-t", target, "pane-border-format", " #{pane_title} ")
        tmux("select-pane", "-t", f"{target}.0", "-T", top_name.capitalize())
        tmux("select-pane", "-t", f"{target}.1", "-T", bottom_name.capitalize())
    tmux("select-window", "-t", target)


def start_loops():
    """Both loops in a tmux session, one window each; never duplicates a loop that is already running."""
    has_session = tmux("has-session", "-t", TMUX_SESSION)[0] == 0
    windows = {}
    if has_session:
        for line in tmux("list-windows", "-t", TMUX_SESSION, "-F", "#{window_name} #{pane_dead}")[1].splitlines():
            name, _, dead = line.partition(" ")
            windows[name] = dead
    for name, config in LOOPS:
        if running_pid(name) is not None or loop_pids(config):
            print(f"{name}: already running")
            continue
        cmd = loop_command(config)
        if not has_session:
            tmux("new-session", "-d", "-s", TMUX_SESSION, "-n", name, cmd)
            # The window stays open when the loop ends: the last message can still be read.
            tmux("set-option", "-t", TMUX_SESSION, "remain-on-exit", "on")
            has_session = True
        elif name in windows:
            tmux("respawn-window", "-k", "-t", f"{TMUX_SESSION}:{name}", cmd)
        else:
            tmux("new-window", "-d", "-t", TMUX_SESSION, "-n", name, cmd)
        print(f"{name}: iniciado")
    open_logs_window(has_session, windows)


def stop_loops():
    """Ctrl+C on both loops: each ends the running unit and saves its state."""
    for name, config in LOOPS:
        pids = {pid for pid in [running_pid(name), *loop_pids(config)] if pid}
        if not pids:
            print(f"{name}: not running")
            continue
        for pid in pids:
            try:
                os.kill(pid, signal.SIGINT)
            except ProcessLookupError:
                pass
        for _ in range(60):
            if running_pid(name) is None and not loop_pids(config):
                break
            time.sleep(1)
        print(f"{name}: parado" if running_pid(name) is None else f"{name}: ainda encerrando (veja o log)")


def cmd_start(args):
    start_loops()
    print(f"follow with: tmux attach -t {TMUX_SESSION}  (Ctrl+B then N switches window)")


def cmd_stop(args):
    stop_loops()


def cmd_status(args):
    """Does not query the model: shows the last quota reading this instance has."""
    cfg = load_config()
    state = load_state(cfg)
    engine = engines.make_engine(cfg)
    limits = engine.limits(state, max_age=float("inf"))
    print("\n".join(status_lines(cfg, engine, state, limits, schedule.from_config(cfg), datetime.datetime.now())))


def cmd_check(args):
    """What the loop would do now, without running any unit or saving the state."""
    cfg = load_config()
    state = load_state(cfg)
    engine = engines.make_engine(cfg)
    win = schedule.from_config(cfg)
    now = datetime.datetime.now()
    limits = engine.limits(state, max_age=0)  # fresh reading (on Claude, a minimal query)
    print("\n".join(status_lines(cfg, engine, state, limits, win, now)))
    print()
    if win and not win.can_start(now):
        print(f"now: no unit starts ({'last minutes of the window' if win.contains(now) else 'outside the window'})")
        return
    if engine.needs_limits and not limits:
        print("now: no unit starts (no quota reading)")
        return
    project, lock, wait = pick(cfg, engine, state, limits)
    coord.unlock(lock)
    if project:
        kind = "would resume" if state[project["name"]]["interrupted"] or coord.is_handoff(project["name"]) else "would start"
        print(f"now: {kind} a unit on {project['name']}")
    else:
        print(f"now: nothing eligible; would check again in {int(wait // 60)} min")


def cmd_decisions(args):
    cfg = load_config()
    found = False
    for p in cfg["project"]:
        for path in decision_files(p):
            d = read_json(path) or {}
            if "answer" in d:
                continue
            found = True
            deadline = time.strftime("%F %T", time.localtime(d["deadline"])) if d.get("deadline") else "not sent yet"
            print(f"{p['name']} / {d.get('id', path.stem)}   deadline {deadline}   default {d.get('default')}")
            print(f"  {d.get('question')}")
            for o in d.get("options") or []:
                print(f"    {o.get('id')}) {o.get('label')}")
    if not found:
        print("no pending decision")


def cmd_answer(args):
    cfg = load_config()
    project = next((p for p in cfg["project"] if p["name"] == args.name), None)
    if not project:
        sys.exit(f"unknown project: {args.name}")
    path = Path(project["path"]) / DECISIONS / f"{args.id}.json"
    d = read_json(path)
    if not d or "answer" in d:
        sys.exit("unknown or already answered decision")
    if args.option not in {o.get("id") for o in d.get("options") or []}:
        sys.exit(f"invalid option; use one of: {', '.join(o.get('id') for o in d['options'])}")
    print(f"{args.name}: {answer_decision(project, path, d, args.option, 'owner')}")


def cmd_test_notify(args):
    cfg = load_config()
    if not (cfg.get("notify") or {}).get("topic"):
        sys.exit("configure [notify].topic em projects.toml")
    ntfy(cfg, "autopilot: teste", "Se você está lendo isto no celular, as notificações funcionam.", tags=["bell"])
    print(f"sent to {ntfy_server(cfg)} (topic in secrets.toml)")


def cmd_reset(args):
    cfg = load_config()
    state = load_state(cfg)
    if args.name not in {p["name"] for p in cfg["project"]}:
        sys.exit(f"unknown project: {args.name}")
    state[args.name].update(status="active", blocked_until=0, errors=0)
    save_state(state)
    print(f"{args.name}: ativo")


def cmd_serve(args):
    import server  # only the panel needs it

    server.serve(HOME, args.port)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="instance config (default: projects.toml)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").set_defaults(func=cmd_run)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("check").set_defaults(func=cmd_check)
    sub.add_parser("start").set_defaults(func=cmd_start)
    sub.add_parser("stop").set_defaults(func=cmd_stop)
    reset = sub.add_parser("reset")
    reset.add_argument("name")
    reset.set_defaults(func=cmd_reset)
    sub.add_parser("decisions").set_defaults(func=cmd_decisions)
    answer = sub.add_parser("answer")
    answer.add_argument("name")
    answer.add_argument("id")
    answer.add_argument("option")
    answer.set_defaults(func=cmd_answer)
    sub.add_parser("test-notify").set_defaults(func=cmd_test_notify)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    serve.set_defaults(func=cmd_serve)
    args = parser.parse_args()
    if args.cmd != "serve":  # the panel reads every instance; it does not pick one
        configure(args.config)
    try:
        args.func(args)
    except Exception:
        if args.cmd == "serve":  # the panel has no log of its own; do not pollute the Codex loop's log
            raise
        # Without this the traceback disappears with the tmux window.
        import traceback
        log("unexpected error; the loop stopped:\n" + traceback.format_exc())
        if args.cmd == "run":
            ntfy(load_config(), "autopilot: parou", "Erro inesperado. Veja o fim do autopilot.log.",
                 priority=5, tags=["rotating_light"])
        raise


if __name__ == "__main__":
    main()
