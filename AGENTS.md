# turno

A scheduler that runs the Codex CLI and Claude Code unattended on several projects, inside the quota
of the subscriptions, plus a local web panel to follow it. It grew out of codex-autopilot and is
becoming an open product. This folder is the tool itself; the projects live elsewhere, and the data
(config, state, logs, runs) lives in `~/.turno` (or `$TURNO_HOME`). Here you maintain the scheduler;
you do not work on the projects. Every data path in this file given without a folder (`shared/`,
`state*.json`, `secrets.toml`, `claude.toml`, logs, `runs/`) lives in `~/.turno` (or `$TURNO_HOME`).

Language: everything in this repository is in English (code, comments, docstrings, logs, CLI output,
docs, commit messages). The exception is what the owner receives at runtime, which stays in
Brazilian Portuguese until there is a language setting: ntfy notifications, decision texts, the
`unit`/`summary`/`milestone` the orchestrator writes, and the panel interface (`web/i18n/pt-BR.js`).

## Rule number one

**Never run `autopilot.py start`, `autopilot.py run` or `autopilot.py --config claude.toml run` from a
Codex or Claude Code session.** The loop starts other sessions, spends the same quota you do and dies
with your session. The owner starts the loops, with `autopilot.py start` (see `README.md`). `status`,
`decisions`, `test-notify` and `serve` (it starts nothing by itself) are safe; so is `check`, but it reads
the quota (on Claude, with a minimal Haiku query). `stop`, `answer` and `reset` change state and only
run when the owner asks. Each engine has an instance lock (`~/.turno/shared/instance-<engine>.lock`,
holding the pid): two loops of the same engine never run. From an agent session, never call the
panel's start, stop or interrupt endpoints (`/api/engines/<engine>/start|stop`,
`/api/projects/<name>/interrupt`) either: they have the same effect as the commands above.

## Files

| Path | What it is |
|---|---|
| `autopilot.py` | CLI, decisions and ntfy, Docker, project choice and the loop (`run_once`, `run_project`) |
| `engines.py` | `CodexEngine` and `ClaudeEngine`: command, quota reading, output classification, cost, live feed translation |
| `coord.py` | Claims, baton and locks between the instances, in `shared/` |
| `schedule.py` | Weekly window (Claude's: Friday 19:00 to Sunday 03:00) |
| `paths.py` | The data folder (`$TURNO_HOME` or `~/.turno`) |
| `history.py` | Unit history (`history-<engine>.jsonl`) |
| `backlog.py` | Validation and reading of each project's `backlog.json` |
| `feed.py` | Incremental reading of run JSONL files and translation into the live feed |
| `panel.py` | What the panel shows, assembled from the files (read-only) |
| `overrides.py` | The panel's requests to the loops (`~/.turno/overrides.json`), read-modify-write under a lock |
| `server.py` | The panel's HTTP server on 127.0.0.1: API, events (SSE) and `web/` |
| `control.py` | What the panel's write routes do (pause, next, interrupt, start/stop, decisions, messages, settings); they need the `X-Turno: 1` header, JSON and a local `Host`/`Origin` |
| `web/` | Interface: `index.html`, `app.js`, `style.css`, `i18n/pt-BR.js` |
| `prompts/unit.md` | Prompt of every new unit: the protocol the orchestrator follows |
| `prompts/resume.md` | Prompt to resume an interrupted unit |
| `prompts/engine-*.md` | Engine note appended to the prompt (what is specific to Codex or Claude) |
| `claude-agents.json` | Claude roles (`planner`, `plan-reviewer`, `implementer`, `reviewer`), passed with `--agents` |
| `*.example.toml` | Config examples; the real `projects.toml`, `claude.toml` and `secrets.toml` live in `~/.turno` |
| `projects.toml` | Codex instance: projects in priority order, caps, models |
| `claude.toml` | Claude instance: `[instance]`, `[engine]`, `[schedule]`, 90% cap |
| `secrets.toml` | ntfy topic, read by both instances (outside git) |
| `overrides.json` | Panel requests: `order`, `projects.<name>.paused`, `projects.<name>.weekly_cap.<engine>`, `primary_cap.<engine>`, `next`, `reset`, `poke` (generated; under `shared/overrides.lock`) |
| `state.json`, `state-claude.json` | State of each instance (generated; do not edit while the loop runs) |
| `autopilot.log`, `autopilot-claude.log` | Log of each instance (generated) |
| `runs/`, `runs-claude/` | JSONL and stderr of the latest runs (generated) |
| `shared/` | Claims (`claims/`), locks (`*.lock`, including `overrides.lock`), `notes/` and `notify.json` (generated) |
| `tests/` | Tests: `python3 -m unittest discover -s tests -v` |
| `~/.codex/agents/*.toml` | Codex roles: `planner`, `plan-reviewer`, `implementer`, `reviewer` |
| `<project>/docs/autopilot.md` | Each project's rules: backlog, paths, verification, git |
| `<project>/.superpowers/autopilot/` | `last-unit.json`, `backlog.json` and `decisions/`, written by the orchestrator |

The owner's projects, their priority and caps are in `projects.toml` and `claude.toml`. Projects that
bind the same Docker ports share a `docker_group`.

## How it works

A **unit** is one step of the spec → plan → execution cycle: apply decision answers, recover an
interrupted task, run a task, finish an item, write a plan, write a spec or propose the backlog. Each
unit is a short CLI session, and the orchestrator ends it by writing
`<project>/.superpowers/autopilot/last-unit.json` with the status `worked`, `waiting`, `idle` or
`blocked`, plus `backlog.json`.

Each instance has an engine (`[engine] kind`, `codex` by default). On every turn, `run_once`:

1. `process_decisions`: under `shared/decisions.lock`, sends new decisions to ntfy, reads the answers
   and applies the default when the deadline passes (the read position lives in `shared/notify.json`);
2. with `[schedule]`, outside the window or in its last `start_margin_minutes`, it only sleeps: the
   quota is not even queried;
3. `engine.limits`: usage of the 5-hour and weekly windows. Codex: the most recent session log in
   `~/.codex/sessions`. Claude: the `rate_limit_event` of the last run, or a minimal Haiku query
   (`--safe-mode`) if the reading is older than 30 min. Without a reading, Claude starts nothing;
4. `pick`: this engine's interrupted unit and any engine's baton go first. Otherwise, the first
   project by priority that is not `idle`, `waiting` or in `blocked` cooldown, not claimed by the
   other engine, with a clean tree, and whose usage plus `safety_factor` × the learned cost fits under
   `weekly_cap` (week) and `primary_cap` (5 h). A project that does not fit does not hold up the line:
   the next, cheaper one may run. It takes the group lock (`shared/<docker_group or name>.lock`);
5. `run_project`: writes the claim, `prepare_docker` (stops the stack of the other project in the
   group unless it has uncommitted work, and brings this project's up);
6. `run_unit` (saves the state, including `unit_id`, `run` and the `_loop` phase, before starting
   the CLI; with a window, cuts at its end) and `after_unit`: classifies the output (`usage_limit`,
   `error`, `ok` or cut), writes one history line, learns the unit cost (exponential moving average),
   releases or passes the claim and updates the state;
7. `nap`: sleeps in 5-second steps; reads ntfy answers once a minute, and wakes at once when
   `overrides.json` changes.

At the start of each turn the loop re-reads `overrides.json` and applies it over the config: project
order, `paused` (a paused project is skipped, even with an interrupted unit, which waits for the resume), the
caps of the panel's "Order and caps" window, `next` (the project that goes first, consumed when it
starts), `reset` (puts a project back to `active`) and `poke` (refreshed on every write, to wake
the nap).

`SIGUSR1` interrupts the running unit: the handler only notes the request and signals the unit's
process group, and never waits. The panel pauses the project first, so the loop does not pick it
again. The unit stays `interrupted` (no cost learned) and is resumed after the project is resumed. The outcome
`stopped` (a history line) covers this and also `stop` (SIGINT/SIGTERM) in the middle of a unit.

Invariants a change must not break:

- **Nothing is left half-done.** `interrupted` only goes back to `False` when the unit closes with
  `last-unit.json`. An interrupted unit is resumed before any other, even above the cap.
- **Someone else's work is not touched.** A project with a dirty tree and no interrupted unit is
  skipped, and its Docker stack is not stopped.
- **Cost is measured once per unit**, only when it closes: a resume does not count twice.
- **ntfy answers only for a decision already sent, unanswered, with a listed option.** Both the
  buttons (`project/decision/option` on the `-reply` topic) and the letter typed in the app on the
  main topic count (`B`, `B, text…`, `myapp B`; the project is required when more than one is
  pending). A message with a title comes from the scheduler itself and is ignored.
- **Free text in the topic is a message from the owner to the agents**, unprotected (the owner's
  choice: whoever knows the topic can send too). It is stored in `shared/notes/<project>.json`, goes
  to the projects named in it (none named = all), is appended to the next unit's prompt ("Owner
  messages") and only counts as delivered when that unit closes. No message overrides step 7
  ("Never") of `prompts/unit.md`.
- **A network or Docker failure never brings the loop down.** It logs, warns once and retries.
- **Never two engines on the same project or Docker group.** The claim (`shared/claims/`) lasts until
  the unit closes, including while interrupted; the group lock holds while the CLI runs.
- **The baton goes first.** A unit cut at the end of the window becomes an `any` claim, carrying the
  `unit_id`: any engine resumes it before starting another, even with a dirty tree, in a new session
  from the ledger.
- **Claude only works inside its window and with the quota read.** Outside it, it does not even query
  the quota.
- **The panel writes only three things.** `overrides.json` (under `shared/overrides.lock`), decision
  answers in the decision files (under `shared/decisions.lock`) and the owner's notes (through
  `coord.add_note`). It also starts and stops the loops through tmux and signals. It never edits the
  state files, the `.toml` files or the projects, and never loads `secrets.toml`. It listens only on
  `127.0.0.1`; write routes need `X-Turno: 1`, a JSON body and a local `Host` and `Origin`.
- **The panel's controls:** start and stop per engine; the "Needs you" strip (pending decisions and
  messages); pause, next and interrupt on each project card; the project panel with a message and
  caps; and the "Order and caps" window.
- **History per run.** Every finished run becomes one line in `history-<engine>.jsonl`; summary and
  cost only on the run that closes the unit.

## Decisions from the phone

The orchestrator writes `<project>/.superpowers/autopilot/decisions/<id>.json`, with 2 or 3 options
and a `default`, and ends the unit as `waiting`. The scheduler adds `notified_at` and `deadline`, and
later `answer`, `answered_by` (`owner`, `timeout` or `invalid-format`) and `answered_at`. The next unit
applies the answer and writes `"applied": true`. The full format is in step 6 of `prompts/unit.md`,
and the validation in `valid_decision`.

The `topic` in `secrets.toml` is a secret: never print it in logs, commits or messages.

## When changing something

- **Protocol changes** (`prompts/`, roles, `last-unit.json`, `backlog.json`, decision files): keep
  `prompts/unit.md`, the engine notes (`prompts/engine-*.md`), the roles in `~/.codex/agents/` and
  `claude-agents.json`, and each project's `docs/autopilot.md` consistent.
- **Tests:** `python3 -m unittest discover -s tests -v`. `tests/helpers.py` loads the autopilot with
  state, log, `shared/` and ntfy in a temporary folder (`TURNO_HOME`), and provides a `FakeEngine`, a
  test git repository and a sample data folder (`make_home`). New tests follow that pattern. Never
  test against the real `state.json`, call the real `codex` or `claude`, or stop the projects'
  containers. Never run `autopilot.py` commands without `TURNO_HOME` pointing at a temporary folder,
  except the safe ones above against the real data.
- After editing: the tests,
  `python3 -m py_compile autopilot.py coord.py engines.py schedule.py paths.py history.py backlog.py feed.py panel.py server.py overrides.py control.py`,
  `python3 autopilot.py status` and `python3 autopilot.py --config claude.toml status`.
- If the loop is running, a change only applies after the owner restarts it. Tell them.
- This folder is under git. Large changes go on a branch.
