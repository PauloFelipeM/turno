# turno

A scheduler that runs the Codex CLI and Claude Code unattended on several projects, inside the quota
of the subscriptions, plus a local web panel to follow it. It grew out of codex-autopilot and is
becoming an open product. This folder is the tool itself; the projects live elsewhere, and the data
(config, state, logs, runs) lives in `~/.turno` (or `$TURNO_HOME`). Here you maintain the scheduler;
you do not work on the projects. Every data path in this file given without a folder (`shared/`,
`state*.json`, `config.json`, `secrets.toml`, logs, `runs/`) lives in `~/.turno` (or `$TURNO_HOME`).

Language: everything in this repository is in English (code, comments, docstrings, logs, CLI output,
docs, commit messages). The exception is what the owner receives at runtime, which stays in
Brazilian Portuguese until there is a language setting: ntfy notifications, decision texts, the
`unit`/`summary`/`milestone` the orchestrator writes, and the panel interface (`web/i18n/pt-BR.js`).

## Rule number one

**Never run `autopilot.py start`, `autopilot.py run` or `autopilot.py --engine claude run` from a
Codex or Claude Code session.** The loop starts other sessions, spends the same quota you do and dies
with your session. The owner starts the loops, with `autopilot.py start` (see `README.md`). `test-notify` and
`serve` (it starts nothing by itself) are safe. So are `status`, `decisions` and `check`, with one
caveat: the first time, when `config.json` does not exist yet, any command that loads the config
imports `projects.toml` and `claude.toml` and writes `config.json` (see "The config"). `check` also
reads the quota (on Claude, with a minimal Haiku query). `stop`, `answer` and `reset` change state and only
run when the owner asks. Each engine has an instance lock (`~/.turno/shared/instance-<engine>.lock`,
holding the pid): two loops of the same engine never run. From an agent session, never call the
panel's start, stop or interrupt endpoints (`/api/engines/<engine>/start|stop`,
`/api/projects/<name>/interrupt`) either: they have the same effect as the commands above.

## Files

| Path | What it is |
|---|---|
| `autopilot.py` | CLI, decisions and ntfy, Docker, project choice and the loop (`run_once`, `run_project`) |
| `engines.py` | `CodexEngine` and `ClaudeEngine`: command, quota reading, output classification, cost, live feed translation (the contract is in `docs/engines.md`) |
| `config.py` | `config.json`: validation, atomic writes under `shared/config.lock`, the one-time import of the old TOML files, `for_engine` |
| `coord.py` | Claims, baton and locks between the instances, in `shared/` |
| `schedule.py` | Weekly window (Claude's: Friday 19:00 to Sunday 03:00) |
| `paths.py` | The data folder (`$TURNO_HOME` or `~/.turno`) |
| `history.py` | Unit history (`history-<engine>.jsonl`) |
| `backlog.py` | Validation and reading of each project's `backlog.json` |
| `feed.py` | Incremental reading of run JSONL files and translation into the live feed |
| `panel.py` | What the panel shows, assembled from the files (read-only) |
| `overrides.py` | The panel's requests to the loops (`~/.turno/overrides.json`), read-modify-write under a lock |
| `repo_setup.py` | Setup requests (`~/.turno/setup/<project>.json`, under `shared/setup.lock`), the draft reader (never through a symlink) and the `.git/info/exclude` line |
| `server.py` | The panel's HTTP server on 127.0.0.1: API, events (SSE) and `web/` |
| `control.py` | What the panel's write routes do (pause, next, interrupt, reactivate, start/stop, decisions, messages, projects, engines, settings); they need the `X-Turno: 1` header, JSON and a local `Host`/`Origin` |
| `web/` | Interface: `index.html`, `app.js`, `style.css`, `i18n/pt-BR.js` (including the settings screen, with Projects and Engines) |
| `docs/engines.md` | The engine contract: what the loop calls on an engine, and what a CLI must offer |
| `prompts/unit.md` | Prompt of every new unit: the protocol the orchestrator follows |
| `prompts/resume.md` | Prompt to resume an interrupted unit |
| `prompts/setup.md` | Prompt of a setup unit: draft mode (rules, `AGENTS.md`, initial backlog, `notes.md` in the draft folder) and apply mode (copy, one commit `docs: prepare for turno`) |
| `prompts/engine-*.md` | Engine note appended to the prompt (what is specific to Codex or Claude) |
| `claude-agents.json` | Claude role prompts (`planner`, `implementer`, `reviewer`); the engine adds the configured model and effort and passes them with `--agents` |
| `config.json` | The configuration, written by the panel and re-read by the loops on every turn: projects (their order is the priority, weekly cap per engine) and a section per engine (on/off, orchestrator model and effort, window, caps, costs). Under `shared/config.lock` |
| `config.example.json` | An example config with generic projects; the real one lives in `~/.turno` |
| `secrets.toml`, `secrets.example.toml` | ntfy topic, read by both instances (outside git; the panel never loads it) |
| `projects.toml`, `claude.toml` | Legacy. Imported once, when `config.json` does not exist, and kept as a copy; nothing reads them afterwards |
| `overrides.json` | Panel requests: `projects.<name>.paused`, `next`, `reset`, `poke` (generated; under `shared/overrides.lock`). Order and caps used to live here; they are in `config.json` now, and the import leaves this file untouched |
| `setup/<project>.json` | A project's setup request: `goal`, `status` (`requested`, `review`, `approved`, `done`), `feedback` and timestamps (written by the panel and the loops, under `shared/setup.lock`) |
| `state.json`, `state-claude.json` | State of each instance (generated; do not edit while the loop runs) |
| `autopilot.log`, `autopilot-claude.log` | Log of each instance (generated) |
| `runs/`, `runs-claude/` | JSONL and stderr of the latest runs (generated) |
| `shared/` | Claims (`claims/`), locks (`*.lock`, including `overrides.lock`), `notes/` and `notify.json` (generated) |
| `tests/` | Tests: `python3 -m unittest discover -s tests -v` |
| `codex-agents/*.toml` | Codex role templates (`planner`, `implementer`, `reviewer`), without model or effort |
| `$CODEX_HOME/agents/*.toml` | Generated by turno from the templates plus `engines.codex.roles` before each Codex unit; never edit by hand |
| `<project>/docs/autopilot.md` | Each project's rules: backlog, paths, verification, git |
| `<project>/.superpowers/autopilot/` | `last-unit.json`, `backlog.json` and `decisions/`, written by the orchestrator |
| `<project>/.superpowers/autopilot/setup/` | The setup draft, written by the agent; deleted by the apply unit |

## The config

The owner's projects, their priority and caps, and each engine's settings are in `config.json`. Each
project is registered once: the order of the list is the priority for every engine, and `weekly_cap`
is per engine (a missing value means 100; 0 means the engine never starts a new unit there). Projects
that bind the same Docker ports share a `docker_group`. `config.load` validates the file; the loops
call it on every turn.

- **First run.** When `config.json` does not exist and `projects.toml` does, `config.load` imports
  `projects.toml` (Codex) and `claude.toml` (Claude, when present), writes `config.json` and leaves the
  TOML files as a copy. The engine whose file exists is enabled. `overrides.json` is not changed.
- **Invalid file.** A `config.json` that does not validate never stops a loop: it keeps the last good
  configuration and logs the problems once per change of the file. The panel's writes are validated
  before they replace the file.
- **Engine off.** A disabled engine starts no unit. Its loop, if running, sleeps with phase `disabled`,
  keeps the ntfy decisions flowing and passes its open units on as batons.
- **Loops.** `autopilot.py --engine codex|claude run` runs one loop (`--config` is a deprecated alias
  of `--engine`). `start` and `stop` use the engines enabled in the config.

## How it works

A **unit** is one step of the spec → plan → execution cycle: apply decision answers, recover an
interrupted task, run a task, finish an item, write a plan, write a spec or propose the backlog. Each
unit is a short CLI session, and the orchestrator ends it by writing
`<project>/.superpowers/autopilot/last-unit.json` with the status `worked`, `waiting`, `idle` or
`blocked`, plus `backlog.json`.

Each instance runs one engine (`--engine`, `codex` by default; the contract is in `docs/engines.md`). On every turn, `run_once`:

1. `process_decisions`: under `shared/decisions.lock`, sends new decisions to ntfy, reads the answers
   and applies the default when the deadline passes (the read position lives in `shared/notify.json`);
2. with a `schedule` in the engine's config, outside the window or in its last `start_margin_minutes`, it only sleeps: the
   quota is not even queried;
3. `engine.limits`: usage of the 5-hour and weekly windows. Codex: the most recent session log in
   `~/.codex/sessions`. Claude: the `rate_limit_event` of the last run, or a minimal Haiku query
   (`--safe-mode`) if the reading is older than 30 min. Without a reading, Claude starts nothing;
4. `pick`: this engine's interrupted unit and any engine's baton go first. Otherwise, the first
   project by priority that is not `idle`, `waiting` or in `blocked` cooldown, not claimed by the
   other engine, with a clean tree, and whose usage plus `safety_factor` × the learned cost fits under
   `weekly_cap` (week) and `primary_cap` (5 h). A project that does not fit does not hold up the line:
   the next, cheaper one may run. In both passes a project whose `retry_at` is still in the future
   is skipped, interrupted unit or not.
   A project whose setup request is `review` is skipped; one whose request is `requested` or
   `approved` gets a setup unit (`prompts/setup.md`) instead of a normal one, even if it is `idle` or
   `waiting`; an `approved` one runs its apply unit even with a dirty tree (the prompt decides). The
   `.superpowers/` line goes into its `.git/info/exclude` (unless git already ignores it) in `run_unit`
   and, in `pick`, only when the tree is dirty, so the draft does not count as someone else's changes.
   It takes the group lock (`shared/<docker_group or name>.lock`);
5. `run_project`: writes the claim, `prepare_docker` (stops the stack of the other project in the
   group unless it has uncommitted work, and brings this project's up). If the ports are busy with
   someone's uncommitted work, only this project waits (`retry_at`, 10 min) and the queue moves on
   to the next one; if Docker is down or compose fails, the whole loop waits 10 min and warns once;
6. `run_unit` (saves the state, including `unit_id`, `run` and the `_loop` phase, before starting
   the CLI; with a window, cuts at its end) and `after_unit`: classifies the output (`usage_limit`,
   `error`, `ok` or cut), writes one history line, learns the unit cost (exponential moving average),
   releases or passes the claim and updates the state. After `usage_limit` or `error` it writes
   `retry_at`, the time before which the project does not run again;
7. `nap`: sleeps in 5-second steps; reads ntfy answers once a minute, and wakes when
   `overrides.json` has changed since `run_once` last read it (so a request written between the
   read and the nap is not lost).

`retry_at` (per project, in the state file) is the wait after an error, a usage limit or a busy
Docker. Waking early does not cancel it: a click on the panel wakes the loop, but `pick` still skips
the project, even to resume its interrupted unit, until that time. It goes back to 0 when the unit
closes, when a later run ends another way (unfinished, cut, stopped) and on a reset: the panel's
"Reativar" and "Tentar agora" (both write `reset`) and `autopilot.py reset` clear it. The panel
shows it on the project card ("tenta de novo ...").

### Preparing a project

A project without `docs/autopilot.md` can be prepared from the panel: the owner writes a goal and
the panel creates `setup/<project>.json` (`requested`). The next unit on that project is a draft unit
(`prompts/setup.md`, with a "Setup unit" section appended: mode, goal, feedback, draft files); it
writes the draft in `.superpowers/autopilot/setup/` and closes `waiting`. If `setup/autopilot.md`
exists, the request goes to `review`, the project stays `waiting` and the owner is told by ntfy. The
owner approves (`approved`) or asks for changes (`requested` with `feedback`). The apply unit copies
the files, commits `docs: prepare for turno` and deletes the draft; when it closes `worked` with
`docs/autopilot.md` in place the request is `done` and the project is `active`. A setup unit that
closes without its result is `blocked`, keeping the phase. The loop moves a request only from the
phase the unit started with (`setup.transition`), so a cancel during the unit wins.

At the start of each turn the loop re-reads `config.json` (projects, order, caps and engine settings;
model and effort apply to the next unit) and `overrides.json`, and applies the latter over the former:
`paused` (a paused project is skipped, even with an interrupted unit, which waits for the resume), `next` (the next *new* unit goes to this project if it
fits the quota, after the interrupted units and the batons; the key is consumed when that unit
really starts, with Docker up), `reset` (puts a project back to `active`) and `poke` (refreshed on
every write, to wake the nap). A file that exists but is not a JSON object counts as no overrides
and is reported in the log once per change of the file.

`SIGUSR1` interrupts the running unit: the handler only notes the request and signals the unit's
process group, and never waits. `cmd_run` installs the handlers before it takes the instance lock,
so the pid in the lock file always belongs to a process that handles the signal. Before sending it,
the panel checks in `/proc/<pid>/status` (`SigCgt`) that the loop has a `SIGUSR1` handler: without
one the signal's default action would end the loop, so a loop started by an older version answers
409 `restart_needed` and has to be stopped and started again before it accepts the interrupt. The
order is: check, pause the project (so the loop does not pick it again), signal. The unit stays
`interrupted` (no cost learned) and is resumed after the project is resumed. The outcome `stopped`
(a history line) covers this and also `stop` (SIGINT/SIGTERM) in the middle of a unit.

Invariants a change must not break:

- **Nothing is left half-done.** `interrupted` only goes back to `False` when the unit closes with
  `last-unit.json`. An interrupted unit is resumed before any other, even above the cap. Two things
  suspend "the interrupted unit goes first" for one project, without closing the unit: a pause from
  the panel (until the owner resumes it) and a `retry_at` in the future.
- **A wait is not cancelled by waking early.** After an error, a usage limit or a busy Docker the
  project does not run before `retry_at`, however many times the panel wakes the loop.
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
  from the ledger. Two exceptions: a disabled engine passes its open units on as batons instead of
  resuming them, and an engine does not take the baton of a project whose cap is 0 for it. An engine's own `retry_at` for that project (error, usage limit or busy Docker)
  holds the baton too until it passes; a reset clears it.
- **A paused project keeps its claim.** Pausing skips the project but does not release the claim of
  its open unit: the other engine does not start work there. The exception is a disabled engine,
  which passes the claim on (as a baton) even for a paused project.
- **The loop never ends by itself.** With every project `idle`, or no project at all, it warns once
  (never for an empty list) and keeps sleeping; a reset or a change in the config wakes it.
- **Claude only works inside its window and with the quota read.** Outside it, it does not even query
  the quota.
- **What the panel writes.** `config.json` (under `shared/config.lock`; validated before it
  replaces the file) and `overrides.json` (under `shared/overrides.lock`), decision answers in
  the decision files (under `shared/decisions.lock`) and the owner's notes (through
  `coord.add_note`), and setup requests (`setup/<project>.json`, under `shared/setup.lock`; removing
  a project deletes its request); answering a decision also adds a line to the log (`autopilot.answer_decision`).
  It also starts and stops the loops through tmux and signals. It never edits the state files, the
  projects, and never loads `secrets.toml`. It never edits the legacy TOML files. It listens only on `127.0.0.1`;
  write routes need `X-Turno: 1`, a JSON body and a local `Host` and `Origin`.
- **What turno changes in a project.** Only one line: `.superpowers/` in the clone's
  `.git/info/exclude`, in `run_unit` before a setup unit (and in `pick` only when the tree is dirty), and
  only when git does not already ignore that folder.
  Everything else in a project (the draft, the copied files, the commit) is written by the agent.
  The panel reads the draft files only inside `.superpowers/autopilot/setup/`, never through a
  symlink, at most 200 KB each.
- **The panel's controls:** the settings screen ("⚙ Configurações"), with Projects (add, edit, reorder,
  remove; the name is never changed) and Engines (on/off, models, window, caps, and a check of the CLI
  and the quota); start and stop per engine; the "Needs you" strip, with the pending
  decisions (answer, with an optional comment) and the blocked projects (reactivate, which writes
  `reset`); pause, next and interrupt on each project card; the project panel with a message to the
  agents, the caps and, while the project waits on a `retry_at`, "Tentar agora" (also `reset`); "Preparar com um agente" in the settings screen for a project without `docs/autopilot.md`, and the setup review (approve, ask for changes, cancel) in the project panel.
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

- **Engine changes** (a new engine, or a change in what the loop calls): keep `docs/engines.md`,
  `engines.ENGINES`, `config.ENGINES`/`ENGINE_DEFAULTS` and `tests/helpers.FakeEngine` consistent.
- **Protocol changes** (`prompts/`, including `prompts/setup.md`, roles, `last-unit.json`, `backlog.json`, decision files): keep
  `prompts/unit.md`, the engine notes (`prompts/engine-*.md`), the role templates in `codex-agents/` and
  `claude-agents.json`, and each project's `docs/autopilot.md` consistent.
- **Tests:** `python3 -m unittest discover -s tests -v`. `tests/helpers.py` loads the autopilot with
  state, log, `shared/` and ntfy in a temporary folder (`TURNO_HOME`), and provides a `FakeEngine`, a
  test git repository and a sample data folder (`make_home`). New tests follow that pattern. Never
  test against the real `state.json`, call the real `codex` or `claude`, or stop the projects'
  containers. Never run `autopilot.py` commands without `TURNO_HOME` pointing at a temporary folder,
  except the safe ones above against the real data.
- After editing: the tests,
  `python3 -m py_compile autopilot.py config.py coord.py engines.py schedule.py paths.py history.py backlog.py feed.py panel.py server.py overrides.py control.py repo_setup.py`,
  `python3 autopilot.py status` and `python3 autopilot.py --engine claude status`, both with
  `TURNO_HOME` pointing at a temporary folder (or at the real data, if the owner's `config.json`
  already exists).
- If the loop is running, a change only applies after the owner restarts it. Tell them.
- This folder is under git. Large changes go on a branch.
