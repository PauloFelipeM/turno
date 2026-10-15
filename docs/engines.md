# Engine contract

Code paths below (`engines.py`, `autopilot.py`, `prompts/`, `claude-agents.json`, `codex-agents/`)
are inside the `turno/` package.

An engine is the class that adapts one agent CLI to the loop. `CodexEngine` and `ClaudeEngine` in
`engines.py` are the two that exist; `tests/helpers.FakeEngine` is the smallest valid one. The loop
(`autopilot.py`) never looks inside an engine: it only calls the methods below.

Terms used here: `cfg` is the engine's config in the shape `config.for_engine` returns
(`cfg["defaults"]` holds the model, effort and caps); `state` is the instance's state dict; `s` is
`state[<project>]`, one project's entry.

## Attributes

| Attribute | Type | What it is for |
|---|---|---|
| `name` | `str` | The engine's key: `--engine <name>`, `config.ENGINES`, the file names (`state-<name>.json`, `runs-<name>/`), `prompts/engine-<name>.md`, claims and history |
| `label` | `str` | The name in ntfy texts and the log (`Codex`, `Claude`) |
| `needs_limits` | `bool` | `True`: with no quota reading the loop starts nothing (`Claude`, whose cap cannot be honoured blind). `False`: it goes on (`Codex`) |

## Methods

`__init__(self, cfg, home=None)` builds the engine; `home` is the CLI's own data folder (tests point
it elsewhere). The loop sets `engine.cfg = cfg` after every reload of `config.json`, so a model or
effort change applies to the next unit.

| Method | Called | Arguments and return |
|---|---|---|
| `describe()` | Once, when the loop starts (one log line) | Returns a short `str`: the binary found and the orchestrator model and effort. Finds the binary, so it exits with a message when the CLI is not installed |
| `limits(state, max_age)` | Every turn of `run_once` (default `max_age`); at the start of a window with `max_age=0` when the window is active, `inf` otherwise; by `status` (`inf`), `check` (`0`, `inf` when disabled) and the panel's engine check (`0`) | Returns the usage as `{"primary": {"used_percent", "resets_at"}, "secondary": {...}}`, `primary` being the 5-hour window and `secondary` the weekly one (`resets_at` in epoch seconds); an engine may add other keys (Claude adds `extra`). Returns `{}` when it cannot read. `max_age` is how old a cached reading may be, in seconds. May write its cache in `state` |
| `resumable(s)` | `run_unit`, before building the prompt | Returns `bool`: whether `s`'s session can be resumed. `True` picks `prompts/resume.md` and the resume command; `False` starts a new unit (`prompts/unit.md`, or `prompts/setup.md` while the project has a setup request; a setup unit also gets a "Setup unit" section, on a resume too) |
| `begin(s, resume)` | `run_unit`, just before `command` | Sets up the session in `s["session"]` when the engine chooses the id itself (Claude does, so a cut never loses it). Codex does nothing: the id only shows up in the output. Runs before the state is saved |
| `command(state, project, s, resume, stem)` | `run_unit` | Returns `(argv, env)`: the command as a list and the environment (`None` to inherit). The prompt is written to the process's stdin, and the working directory is the project's path. `stem` is the path of the run without extension (`runs/<stamp>-<project>`): the loop redirects stdout to `<stem>.jsonl` and stderr to `<stem>.err`, and the engine may put extra files next to it (`<stem>-agents.json`) |
| `after_run(state, s, out, resume)` | After every run, also one cut by the window, stopped from the panel or ended by Ctrl+C or `SIGTERM` | Reads the run's output (`out` is the `.jsonl` path) to record what the next call needs: Codex stores the thread id in `s["session"]` on a new unit; Claude records the quota events in `state` and notes the quota at the unit's start in `s["unit_start_limits"]`. Must not raise on a short or empty file |
| `classify(out, err, code)` | After a run that was not cut or stopped | Returns `(status, reset, hard)`. `status` is `"usage_limit"`, `"error"` or `"ok"`. `reset` is the epoch time when the limit ends, or `None`. `hard` says the limit is a real exhaustion, not a passing rate limit (it picks the default wait when `reset` is `None`). `err` is the `.err` path, `code` the exit code. Only `ok` goes on to read `last-unit.json` |
| `unit_cost(state, s)` | Once per unit, when it closes with `last-unit.json` (never on a resume that does not close) | Returns `{"primary": pp, "secondary": pp}`, the percentage points the whole unit spent, or `None` when it cannot measure (a window reset in the middle, no readings). `None` leaves the learned cost as it was. The loop folds the value into a moving average |
| `close_unit(s)` | When the unit closes, and when it is passed on as a baton | Clears the per-unit fields the engine put in `s` (Claude: `unit_start_limits`) |
| `feed_items(ev)` | By the panel, for each event of a run's JSONL (`feed.translate`); it runs in the panel's process, not the loop's | Static method. Returns a list of live feed items, `[]` for an event with nothing to show. See "Live feed" |

The loop also reads `s["session"]`, `s["interrupted"]`, `s["unit_id"]` and the rest of `s`. An engine
may only set `session` (in `begin` or `after_run`) and its own fields; it must not touch the others.

### Exit codes the loop handles itself

Before `classify`, the loop recognises the end of the window (`CUT`) and a stop from the panel
(`STOPPED`). Neither reaches the engine's `classify`; `after_run` does run.

### Live feed

`feed_items` returns dicts with a `kind`, which the panel draws:

| `kind` | Other keys | Meaning |
|---|---|---|
| `message` | `text` | What the orchestrator said |
| `command` | `text` | A shell command started |
| `command_done` | `text`, `exit_code`, `output` | The command finished (`output` is a tail) |
| `file` | `text` | A file was changed (the path) |
| `role` | `role`, `text` | A subagent was called (Claude only; Codex does not say which) |
| `wait` | | Waiting for a subagent |
| `error` | `text` | An error in the CLI |
| `unknown` | `text` | An event the engine does not know: the panel shows it dimmed |

An exception in `feed_items` becomes `unknown`: a new CLI format never brings the feed down.

## Registering an engine

1. Write the class in `engines.py` and add it to `engines.ENGINES` (`{"codex": CodexEngine,
   "claude": ClaudeEngine}`). `make_engine` and the feed read it from there.
2. Add its name to `config.ENGINES` and its defaults to `config.ENGINE_DEFAULTS` (the model, the
   effort and any engine-specific setting, such as `max_subagents` for Codex). `config.validate`,
   `for_engine`, `start`/`stop`, the panel's engines list and the settings screen use these.
3. Write `prompts/engine-<name>.md`, the note appended to every prompt (what is specific to the CLI),
   and the subagent roles in the CLI's own format, kept consistent with the others (see "When
   changing something" in `AGENTS.md`).
4. Add the files that depend on the name: `state-<name>.json`, `autopilot-<name>.log`, `runs-<name>/`
   and `history-<name>.jsonl` come from `autopilot.configure`, which today knows the Codex names
   (no suffix) and uses the suffix for the other engines.
5. Test it against `tests/helpers.FakeEngine`'s shape, with a fake binary: never the real CLI.

The panel's start/stop, the `--engine` choices and the settings screen follow `config.ENGINES`.
Strings in `web/i18n/*.js` (every language) that name an engine (and the Claude-only window
defaults) are still written per engine.

## What the protocol requires of a CLI

| Requirement | Why |
|---|---|
| **Non-interactive mode** that takes the prompt on stdin, runs the whole unit and exits with a code | The loop starts one process per run and waits for it. Codex: `codex exec ... -`. Claude: `claude -p` |
| **No permission prompts** | Nobody is there to answer. Both engines run with their "bypass" flag; the protocol's fixed rules (`prompts/unit.md`, step 7) are what keep it safe |
| **Subagents with the three roles** (`planner`, `implementer`, `reviewer`), each with its own model and effort | The method delegates planning, review and implementation; the orchestrator checks the plan itself. Model and effort per role come from `engines.<engine>.roles` in `config.json`. Codex: turno writes `$CODEX_HOME/agents/<role>.toml` from the templates in `codex-agents/` before each unit. Claude: `claude-agents.json` (prompts only) plus the configured model and effort go through `--agents` |
| **A session that can be resumed** by id | A unit cut, interrupted or ended early continues in the same session. When the session does not resume, the loop restarts from the ledger in a new one |
| **JSONL on stdout** (one JSON event per line) | The panel's live feed reads it; `after_run` and `classify` read it too. Codex: `--json`. Claude: `--output-format stream-json --verbose` |
| **A way to tell usage exhaustion from an error**, in the output or the exit code | `classify` must return `usage_limit` with the reset time when it can: the wait ends at the reset, not after a guess |
| **A quota reading**, for the 5-hour and weekly windows, before and after a unit | `limits` and `unit_cost`. Codex writes it in its session logs; Claude reports it in a stream event and can be asked with a minimal query |

## What does not exist yet

**A degraded mode for a CLI without a quota reading.** Today `limits` must return the two windows,
or `{}`. With `needs_limits = False`, `{}` makes the caps unenforceable: usage counts as 0% and every
project fits. With `needs_limits = True`, nothing starts. There is no third mode, such as a fixed
number of units per day or per week, and no cost in anything but percentage points of a window.
The `Engine` is also not an abstract base class: the contract is this document and `FakeEngine`.
