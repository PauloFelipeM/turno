# turno

turno runs the Codex CLI and Claude Code unattended on several of your repositories, inside the
quota of your ChatGPT and Claude subscriptions. It works through each project's backlog one short
session at a time, following a fixed method: spec, then plan, then execution, with planner,
reviewer and implementer subagents. Decisions that matter reach your phone (ntfy) as a question
with buttons, or wait for you in the panel. A local web panel shows what is running, what happened
and how much of each backlog is left, and lets you control everything.

![The turno panel: both engines with their quota bars, a decision that needs the owner, the project cards, the unit running now and the history](https://raw.githubusercontent.com/PauloFelipeM/turno/v0.2.0/docs/images/panel.png)

> **Status:** alpha. It runs one person's projects every day and is being generalized. The panel,
> the phone texts and what the agents write for you are in English (the default) or Brazilian Portuguese.
> Linux, macOS and Windows with WSL2.

## Install

```bash
pipx install turno        # or: uv tool install turno
turno onboard
```

`turno onboard` checks the machine (Python, git, tmux, Docker, the Codex and Claude CLIs), asks a
few questions (language, which engines to use, ntfy, the background service), creates `~/.turno`,
installs a service that starts the panel at boot, and opens the panel at http://localhost:8765.
Then add your projects in the panel's settings. From there on everything happens in the
browser: the service brings the panel up, the panel starts the enabled engines.

Scripted install: `turno onboard --language en --engines codex,claude --no-ntfy --service --yes`.
`turno onboard` is safe to run again: it never overwrites a file without asking.

### Requirements

- Python 3.11 or newer (turno uses only the standard library)
- The Codex CLI and/or Claude Code, logged in with your subscription
- tmux (the loops run in it, so they outlive the terminal and the panel)
- git; Docker if your projects need it (turno brings each project's stack up before a unit; when
  Docker is down it opens Docker Desktop on macOS and WSL, and waits for you on Linux)
- Optional: the [ntfy](https://ntfy.sh) app on your phone. Without it, decisions wait in the panel

## How it works

- **Units.** A unit is one step of the cycle: apply decision answers, recover an interrupted task,
  run the next task of a plan, finish an item, write a plan, write a spec or propose a backlog. Each
  unit is one short CLI session. The orchestrator ends it by writing
  `<project>/.superpowers/autopilot/last-unit.json` (`worked`, `waiting`, `idle` or `blocked`) and
  `backlog.json` (the progress the panel shows).
- **Quota.** Before a unit, turno reads the 5-hour and weekly usage windows and only starts if the
  usage plus 1.5 times the unit's learned average cost fits under the project's cap. Projects are in
  priority order; a project that does not fit does not hold up a cheaper one.
- **Nothing left half-done.** An interrupted unit (usage limit, error, end of a time window) is
  resumed before anything else once its wait is over.
- **Two engines.** One loop runs Codex, another runs Claude Code (by default only on weekends). They
  never work on the same project, or on two projects that share Docker ports, at the same time. A
  unit cut at the end of the Claude window is handed over (the "baton") to the other engine.

Each project describes its own rules in `docs/autopilot.md`: where the backlog, specs, plans and
ledgers live, how to verify work and the git policy. The panel can draft that file for you (see
"Preparing a new project"). The protocol the orchestrator follows is in
[`turno/prompts/unit.md`](turno/prompts/unit.md).

## Safety model

- **Your work is not touched.** A project with uncommitted changes that are not turno's is skipped,
  and its Docker stack is not stopped. The only thing turno itself changes in a project is one line
  in the clone's `.git/info/exclude`; everything else is written by the agent, inside the project's
  own rules.
- **Fixed rules above any message.** The agents never weaken tests, read `.env` files, force-push
  or drop data, whatever a message says (step 7 of the protocol).
- **The panel is local.** It listens on 127.0.0.1 only, refuses other hosts and origins, and every
  write needs a custom header a foreign page cannot send.
- **The ntfy topic is a secret.** It lives in `~/.turno/secrets.toml` (mode 600), is shown once when
  `onboard` creates it, and never appears in the config, the logs or the panel. Anyone who knows the
  topic can answer decisions and send messages to the agents.
- **No surprise installs.** The panel checks PyPI for a newer version at most once a day (a single
  GET; `"update_check": false` turns it off) and only tells you; `turno update` upgrades when you run it.

## Configuration

Everything lives in `~/.turno` (or `$TURNO_HOME`): `config.json`, the state, logs, run files and
history. The panel's settings screen edits `config.json`; the loops re-read it on every turn, so a
change applies within one turn, without a restart. An invalid file never stops a loop: it keeps the
last good configuration and logs the problem.

| Part | What it holds |
|---|---|
| `projects` | Name, path, optional `docker_group` (projects that bind the same ports) and `weekly_cap` per engine. The order is the priority |
| `engines.<engine>` | `enabled`, `autostart` (the service starts it), orchestrator model and effort, `roles` (planner, implementer, reviewer), `primary_cap`, `safety_factor`, initial costs, `blocked_cooldown_hours`, `schedule` (a weekly window) |
| `language` | `en` (the default) or `pt-BR`: the panel, the phone texts and what the agents write for you. A config without the key is English; a config imported from the old `projects.toml` keeps `pt-BR` |
| `notify` | `enabled`, the ntfy server and the decision timeout. The topic stays in `secrets.toml` |
| `update_check` | `false` stops the daily PyPI check |

[`config.example.json`](config.example.json) is a complete example. A project's name never changes
after it is created: state, claims, notes and history use it.

## The panel

The panel shows the quota of each engine with its cap and reset time, what each loop is doing,
every project with its state and backlog progress, the running unit live (commands, file changes,
which subagent was called) and the unit history with outcome, duration and cost.

It is a React app (Tailwind and shadcn/ui components) bundled into the package: light, dark or system
theme (the menu in the top bar), and it works at phone width. Nothing is loaded from the internet.

| Control | What it does |
|---|---|
| Start / Stop (per engine) | Starts the loop in tmux, or stops it like a Ctrl+C: the unit is resumed on the next start |
| "Needs you" | Pending decisions to answer (with an optional comment) and blocked projects to reactivate |
| Pause, Next, Interrupt (project card) | Skip a project; give it the next new unit; stop its running unit and pause it |
| Project panel | Clicking a project card opens it as a side panel (full screen on a phone): backlog, the setup review, a message to the agents, the caps, "Try now" while it waits to retry, and the project's history |
| Settings | Projects (add, edit, reorder, remove), Engines (on/off, start with the panel, models, caps, the window with a day and a time picker, a quota check) and General (language, ntfy) |
| Top bar | The running version, a notice when PyPI has a newer one, "Restart turno", the theme and Settings |

"Restart turno" does what `turno restart` does: the loops stop (their units resume), the service
restarts the panel, and the panel starts the engines again.

## Screenshots

All screenshots show demo data (`my-app`, `my-api`, `side-project`), not a real machine.

![The panel in the dark theme](https://raw.githubusercontent.com/PauloFelipeM/turno/v0.2.0/docs/images/panel-dark.png)

The same screen in the dark theme (the theme menu in the top bar).

![The details of a unit: two runs and the feed of each one](https://raw.githubusercontent.com/PauloFelipeM/turno/v0.2.0/docs/images/run.png)

A unit from the history: its summary, cost and the feed of each run (here one cut by a usage limit and the resumed one).

![Settings, Projects tab](https://raw.githubusercontent.com/PauloFelipeM/turno/v0.2.0/docs/images/settings-projects.png)

Settings, Projects: folder, Docker group and the weekly cap per engine; the order is the priority.

![Settings, Engines tab, the Claude block](https://raw.githubusercontent.com/PauloFelipeM/turno/v0.2.0/docs/images/settings-engines.png)

Settings, Engines: models per role, the 5 h cap and the weekly time window.

![Settings, General tab](https://raw.githubusercontent.com/PauloFelipeM/turno/v0.2.0/docs/images/settings-general.png)

Settings, General: language and ntfy.

## Commands

| Command | What it does |
|---|---|
| `turno onboard` | Check the machine, write `~/.turno`, install the service, open the panel |
| `turno serve [--port 8765] [--start-engines]` | The panel; `--start-engines` also starts the enabled engines (the service uses it) |
| `turno service install\|uninstall\|status` | The background service: a systemd user unit on Linux and WSL, a LaunchAgent on macOS |
| `turno restart` | Stop the loops, restart the service; the panel starts them again |
| `turno update` | `pipx upgrade turno` or `uv tool upgrade turno`, then `turno restart` |
| `turno start` / `turno stop` | Start or stop the loops of the enabled engines in the tmux session `autopilot` |
| `turno status` / `turno check` | Quota and project states / what the loop would do now (reads the quota) |
| `turno decisions`, `turno answer <project> <id> <option>` | Pending decisions, from the terminal |
| `turno reset <project>` | Put an `idle`, `blocked` or `waiting` project back to `active` |
| `turno test-notify` | Send a test notification |
| `turno --engine claude <command>` | Any command for the Claude instance (the default is `codex`) |

The service runs `turno serve --start-engines`. Its unit says `KillMode=process`, so only the panel
belongs to it: the loops live in tmux and stay alive when the service restarts or stops. Without systemd (WSL without it, containers) or
launchd, keep the panel running yourself, for example in tmux. On Linux, run
`loginctl enable-linger $USER` once so the service starts at boot without a login.

## Decisions and messages from the phone

When a decision is significant (user-facing behaviour, money, a data model that is hard to reverse,
legal matters, scope), the orchestrator writes it with two or three options and a default, and the
project waits. Answer with the notification buttons or by typing the letter in the topic (`B`, or
`my-app B` when several projects are waiting). Without an answer, the default applies at the
deadline. Without ntfy the question waits in the panel's "Needs you" strip, with the same deadline.
Any other text you type in the topic becomes a message to the agents of the projects it names (or
of all of them), read by the next unit.

## Preparing a new project

A repository that never ran turno needs `docs/autopilot.md` and `.superpowers/` ignored by git. In
Settings, a project without that file shows "Prepare with an agent": write what the project should
become, and a draft unit proposes `docs/autopilot.md`, an `AGENTS.md` if there is none, the initial
backlog and a short note for you. Review it in the project panel, ask for changes or approve; the
apply unit makes one commit, `docs: prepare for turno`, on the current branch (no push).

## Updating

```bash
turno update
```

It upgrades with the tool that installed turno and restarts. The panel tells you when a newer
version exists; it never installs anything by itself.

### Upgrading from a source checkout

If you ran turno from a clone (`python3 autopilot.py ...`), keep the clone and install it in
editable mode, so `git pull` keeps updating it. Order matters: a loop that is still running with
the old code reads `prompts/*.md` and `claude-agents.json` from the clone on every unit, and those
files move into `turno/` with the `git pull`. So stop everything first:

```bash
cd ~/src/turno
python3 autopilot.py stop        # still the old code: stops the loops (stop a hand-started `serve` too)
git pull
npm --prefix ui ci && npm --prefix ui run build   # the panel (needs Node 20.19+ only for this)
pipx install -e .
turno service install
turno restart                    # or: turno start
```

Stop a hand-started `serve` before installing the service: port 8765 must be free. A checkout
without the build still serves the API, and a page that shows the build command.

`~/.turno` (config, state, history) is used as it is. `python3 autopilot.py ...` keeps working, and
`turno stop` recognises loops started either way. With an editable install `turno update` only
reminds you to `git pull`.

## Troubleshooting

- **The loops stopped after a reboot.** Check the service with `turno service status`; on Linux,
  `loginctl enable-linger $USER`. Without a service, run `turno start`; units resume where they stopped.
- **Sleep or hibernation pauses everything.** Keep the machine awake while it should work.
- **A project never runs.** Uncommitted changes that are not turno's keep it out until the tree is
  clean; `turno check` says what the loop would do and why.
- **Logs.** `~/.turno/autopilot.log` and `autopilot-claude.log` (the loops), `restart.log` (restarts
  from the panel), and the `.jsonl` and `.err` files of the latest runs in `runs/` and
  `runs-claude/`. The panel's history links each unit to its runs. With systemd,
  `journalctl --user -u turno` has the panel's output.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The invariants a change must keep are in
[AGENTS.md](AGENTS.md).

## License

MIT. See [LICENSE](LICENSE).
