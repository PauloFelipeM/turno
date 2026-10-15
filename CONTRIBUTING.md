# Contributing to turno

Thanks for helping. turno runs unattended on people's repositories, so a change is judged first by
what it could break: read the invariants in [AGENTS.md](AGENTS.md) before you start.

## Setup

```bash
git clone https://github.com/PauloFelipeM/turno && cd turno
npm --prefix ui ci && npm --prefix ui run build   # the panel: Node 20.19 or newer, only to build
python3 -m unittest discover -s tests -v
```

turno and its tests use only the Python standard library (3.11 or newer). The panel (`ui/`) is
React + TypeScript + Tailwind + shadcn/ui, built by Vite into `turno/web/` (git-ignored; shipped in
the wheel and the sdist). Node is needed only to build it; `turno serve` without the build answers
with the build command. To try the command itself, install the clone in editable mode in a virtualenv (`python3 -m venv .venv &&
.venv/bin/pip install -e .`) or run `python3 autopilot.py ...` from the root.

## Tests

- Every test runs in a temporary folder through `tests/helpers.py` (`helpers.load(tmp)` sets
  `TURNO_HOME` and loads the package fresh). Never point a test at the real `~/.turno`.
- Tests never call the real `codex`, `claude`, `systemctl`, `launchctl`, `pipx`, `uv`, `tmux` or the
  network: replace `service._run`, `upgrade._run`, `onboard._run`, `pypi._fetch` or
  `autopilot.tmux` (the helpers make the first four fail loudly if a test forgets).
- `TURNO_TEST_WHEEL=1 python3 -m unittest discover -s tests -p test_package.py` also builds the wheel
  and installs it in a throwaway virtualenv (needs pip and the network).
- The panel: `npm --prefix ui run typecheck` and `npm --prefix ui run build`.
  `tests/test_web_texts.py` checks the dictionaries and the source rules (no hard-coded text, no raw
  HTML, no external URL); `tests/test_web_draft.py` and the window round-trip run the shipped
  TypeScript through node (skipped without node). Click through a change in a browser against a
  temporary `TURNO_HOME` (`tests/helpers.make_home`), never against the real data.
- The README's screenshots (`docs/images/`) come from demo data: after a change to the panel, build it and run
  `python3 scripts/screenshots.py` (needs Chrome or Chromium; on WSL it uses the Windows Chrome) to regenerate them.
- Never run `turno start`, `run`, `stop`, `restart` or `update` from a development copy while real
  loops run on the same machine: `stop` finds loops by their command line, whatever `TURNO_HOME` says.

## Style and language

- Code, comments, docstrings, logs, CLI output, docs and commit messages are in English.
- What the owner reads at runtime follows `config.json`'s `language` (`pt-BR` or `en`): the ntfy
  texts in `turno/messages.py`, the panel in `ui/src/i18n/<language>.ts` (both files have the
  same keys) and what the agents write (the prompts' `{owner_language}`). A new text goes into
  both languages.
- Small modules with one responsibility; the standard library only in Python. The panel's texts are in
  `ui/src/i18n/<language>.ts`; English is the default and the fallback; the components use the kit in
  `ui/src/components/ui/` (Radix + cva + tailwind-merge, `forwardRef` because React is 18; do not run
  the shadcn CLI, which emits React 19 components). The panel's build step is the only one, and only
  for the panel.
- Commits follow `type: summary` (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:`).

## Privacy

Use generic names in tests, fixtures and docs (`my-app`, `my-api`, `side-project`,
`/home/me/...`). The maintainer's own design notes (`docs/superpowers/`) describe a private setup
and are ignored by git. Before a release, `scripts/scan_private.py --patterns <your list>` checks
the tree and the history for private names, home paths, topics and tokens; the list itself never
goes into a commit.

## Releasing

In order:

```bash
# 1. bump __version__ in turno/__init__.py
npm --prefix ui ci && npm --prefix ui run build          # 2. the panel, from the lock file
TURNO_TEST_WHEEL=1 python3 -m unittest discover -s tests -p test_package.py   # 3. sdist + wheel carry turno/web
rm -rf dist && python3 -m build                          # 4. sdist and wheel
twine check dist/*                                       # 5.
twine upload -r testpypi dist/*                          # 6. then install from TestPyPI in a clean virtualenv and open the panel
twine upload dist/*                                      # 7. PyPI
```
