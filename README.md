# turno

turno runs the Codex CLI and Claude Code unattended on several of your repositories, inside the
quota of your ChatGPT and Claude subscriptions. It works through each project's backlog one short
session at a time, following a fixed method: spec, then plan, then execution, with planner,
reviewer and implementer subagents. Product decisions that matter reach your phone (ntfy) as a
question with buttons. A local web panel shows what is running, what happened and how much of each
backlog is left.

> **Status:** early. It was built for one person's setup and is being generalized. Phone
> notifications, decision questions and the panel are in Brazilian Portuguese for now; a language
> setting is planned. Linux, macOS and Windows with WSL2 are the targets.

## How it works

- **Units.** A unit is one step of the cycle: apply decision answers, recover an interrupted task,
  run the next task of a plan, finish an item, write a plan, write a spec or propose a backlog. Each
  unit is one short CLI session. The orchestrator ends it by writing
  `<project>/.superpowers/autopilot/last-unit.json` (`worked`, `waiting`, `idle` or `blocked`) and
  `backlog.json` (the progress the panel shows).
- **Quota.** Before starting a unit, turno reads the 5-hour and weekly usage windows and only starts
  if the usage plus 1.5 times the unit's learned average cost fits under the project's cap. Projects
  are in priority order; a project that does not fit does not hold up a cheaper one.
- **Nothing left half-done.** An interrupted unit (usage limit, error, end of a time window) is
  resumed before anything else, once its wait is over: after an error or a usage limit the project
  rests for a while (longer after repeated errors, until the reset after a usage limit), and
  nothing you click in the panel shortens that.
- **Two engines.** One instance runs Codex, another runs Claude Code (by default only on weekends).
  They never work on the same project, or on two projects that share Docker ports, at the same time.
  A unit cut at the end of the Claude window is handed over (the "baton") to whichever engine runs
  next.
- **Your work is safe.** A project with uncommitted changes that are not turno's is skipped.

Each project describes its own rules in `docs/autopilot.md`: where the backlog, specs, plans and
ledgers live, how to verify work and the git policy. The protocol the orchestrator follows is in
`prompts/unit.md`.

## Requirements

- Python 3.11 or newer (standard library only)
- The Codex CLI and/or Claude Code, logged in with your subscription
- tmux, to keep the loops running after you close the terminal
- Docker, if your projects need it (turno brings each project's stack up before a unit)
- The ntfy app on your phone, subscribed to a private topic

## Setup

Configuration, state and logs live in `~/.turno` (or in `$TURNO_HOME`). File names mentioned below
without a folder live there.

```bash
mkdir -p ~/.turno
cp projects.example.toml ~/.turno/projects.toml
cp claude.example.toml ~/.turno/claude.toml
cp secrets.example.toml ~/.turno/secrets.toml
```

Edit `projects.toml` and `claude.toml` with your projects (in priority order) and their weekly caps,
and replace the `topic` in `secrets.toml` with a random name only you know. Then:

```bash
python3 autopilot.py status   # check the config and the current quota
python3 autopilot.py start    # start both loops in the tmux session "autopilot"
python3 autopilot.py serve    # the panel at http://localhost:8765
```

`start` opens one tmux window per loop (`codex`, `claude`) and a `logs` window with both logs.
`stop` ends both loops like a Ctrl+C: the running unit stops, the state is saved and the next
`start` resumes it. `start` never duplicates a loop that is already running.

## Commands

All commands are `python3 autopilot.py <command>`; `--help` lists them. Add `--config claude.toml`
to run a command against the Claude instance.

| Command | What it does |
|---|---|
| `start` / `stop` | Start or stop both loops in tmux |
| `run` | Run a single loop in the foreground (the Codex one, or Claude's with `--config claude.toml`) |
| `status` | Quota usage and each project's state; changes nothing |
| `check` | What the loop would do right now (reads the quota; runs nothing) |
| `decisions` | Pending decisions, with deadline, default and options |
| `answer <project> <id> <option>` | Answer a decision from the terminal |
| `reset <project>` | Put an `idle`, `blocked` or `waiting` project back to `active` |
| `test-notify` | Send a test notification to your phone |
| `serve [--port 8765]` | The panel on 127.0.0.1: follow the loops and control them |

Project states in `status`:

| State | Meaning |
|---|---|
| `active` | In line; runs when it is its turn and there is quota |
| `waiting` | Waiting for your answer to a decision (until the deadline) |
| `blocked` | Stuck on something it cannot solve alone; retried after 6 h |
| `idle` | Backlog finished; does not run again until a `reset` |

## Decisions and messages from the phone

When a decision is significant (user-facing behaviour, money, a data model that is hard to reverse,
legal matters, scope), the orchestrator writes it with two or three options and a default, and the
project waits. You answer with the notification buttons or by typing the letter in the topic (`B`,
or `myapp B` when several projects are waiting). Without an answer, the default applies at the
deadline (`decision_timeout_hours`).

Any other text you type in the topic becomes a message to the agents. It goes to the projects named
in it (or to all of them) and is read by the next unit. A lasting instruction is recorded in the
project's decisions file. Anyone who knows the topic can send messages, so keep it secret; no
message overrides the protocol's fixed rules (never weaken tests, read `.env`, force-push or drop
data).

## The panel

`serve` shows, on one screen: the quota of each engine with its cap and reset time, what each loop
is doing, every project with its state and backlog progress, the running unit live (commands, file
changes, which subagent was called) and the unit history with outcome, duration and cost. It can
stay open while the loops run, and closing it affects nothing. It listens on 127.0.0.1 only and
refuses requests from other origins.

It also controls the loops:

| Control | What it does |
|---|---|
| Start / Stop (per engine) | Starts the loop in tmux, or stops it like a Ctrl+C: the unit stops and is resumed on the next start |
| "Needs you" strip | Pending decisions to answer (with an optional comment) and blocked projects to reactivate |
| Reactivate (blocked project, in the strip) | Puts the project back to `active`, like `reset <project>` |
| Pause / resume (project card) | A paused project is skipped by the loop, even if it has an interrupted unit; the unit waits for the resume |
| Next (project card) | The next *new* unit goes to that project, if it fits the quota; interrupted units and batons still go first |
| Interrupt (project card) | Pauses the project and stops its running unit (`SIGUSR1`); resume it to continue |
| Project panel | A message to the agents, and the project's caps |
| "Order and caps" window | Project order, weekly caps per engine and the 5-hour cap |

The panel never edits state or config. It writes three kinds of file: its requests to the loops, in
`overrides.json` in `~/.turno` (`order`, `projects.<name>.paused`,
`projects.<name>.weekly_cap.<engine>`, `primary_cap.<engine>`, `next`, `reset`, `poke`); decision
answers, in the same decision files the phone uses (each answer is also noted in the log); and
messages to the agents, in the same notes as the phone. The loops re-read `overrides.json` on every
turn and, while asleep, check it every 5 seconds. A unit interrupted from the panel, or by `stop`,
appears in the history as `stopped`. Write requests need the `X-Turno: 1` header and a local origin.

Two things a click does not do:

- **It does not shorten a wait.** After an error, a usage limit or Docker ports held by another
  project's uncommitted work, the project has a retry time (`retry_at` in the state file). A click
  wakes the loop, which then works on the other projects; that one stays out until its time comes.
- **It does not interrupt a loop started by an older version.** Interrupt needs a loop that handles
  `SIGUSR1`; the panel checks first, and if the loop does not, it refuses and asks you to stop and
  start that loop again. Nothing is paused or signalled in that case.

## What stops the loop

- **Sleep or hibernation** pauses everything. Keep the machine awake while it should work.
- **A reboot or `wsl --shutdown`** kills the loops. Run `start` again; it resumes where it stopped.
- **Uncommitted changes in a project** keep that project out until the tree is clean.

## Where to look when something goes wrong

`autopilot.log` and `autopilot-claude.log`, plus the `.jsonl` and `.err` files of the latest runs in
`runs/` and `runs-claude/`. The panel's history links each unit to its runs.

## Development

```bash
python3 -m unittest discover -s tests -v
```

Tests run everything in a temporary folder and never call the real CLIs. See `AGENTS.md` for the
invariants a change must keep.
