# Contributing to turno

Thanks for helping. turno runs unattended on people's repositories, so a change is judged first by
what it could break: read the invariants in [AGENTS.md](AGENTS.md) before you start.

## Setup

```bash
git clone https://github.com/PauloFelipeM/turno && cd turno
python3 -m unittest discover -s tests -v
```

No dependencies: turno and its tests use only the Python standard library (3.11 or newer). To try
the command itself, install the clone in editable mode in a virtualenv (`python3 -m venv .venv &&
.venv/bin/pip install -e .`) or run `python3 autopilot.py ...` from the root.

## Tests

- Every test runs in a temporary folder through `tests/helpers.py` (`helpers.load(tmp)` sets
  `TURNO_HOME` and loads the package fresh). Never point a test at the real `~/.turno`.
- Tests never call the real `codex`, `claude`, `systemctl`, `launchctl`, `pipx`, `uv`, `tmux` or the
  network: replace `service._run`, `upgrade._run`, `onboard._run`, `pypi._fetch` or
  `autopilot.tmux` (the helpers make the first four fail loudly if a test forgets).
- `TURNO_TEST_WHEEL=1 python3 -m unittest discover -s tests -p test_package.py` also builds the wheel
  and installs it in a throwaway virtualenv (needs pip and the network).
- The panel has no JS test runner: check syntax with
  `node --input-type=module --check < turno/web/app.js` and click through the change in a browser.
- Never run `turno start`, `run`, `stop`, `restart` or `update` from a development copy while real
  loops run on the same machine: `stop` finds loops by their command line, whatever `TURNO_HOME` says.

## Style and language

- Code, comments, docstrings, logs, CLI output, docs and commit messages are in English.
- What the owner reads at runtime follows `config.json`'s `language` (`pt-BR` or `en`): the ntfy
  texts in `turno/messages.py`, the panel in `turno/web/i18n/<language>.js` (both files have the
  same keys) and what the agents write (the prompts' `{owner_language}`). A new text goes into
  both languages.
- Small modules with one responsibility; the standard library only; no build step for the panel.
- Commits follow `type: summary` (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`).

## Privacy

Use generic names in tests, fixtures and docs (`my-app`, `my-api`, `side-project`,
`/home/me/...`). The maintainer's own design notes (`docs/superpowers/`) describe a private setup
and are ignored by git. Before a release, `scripts/scan_private.py --patterns <your list>` checks
the tree and the history for private names, home paths, topics and tokens; the list itself never
goes into a commit.

## Releasing

Bump `__version__` in `turno/__init__.py`, then `python3 -m build` and `twine check dist/*`, upload
to TestPyPI, install from it in a clean virtualenv, and only then upload to PyPI.
