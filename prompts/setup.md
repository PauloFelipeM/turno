Autopilot setup unit — project {project}. This session was started by the turno scheduler to
prepare this repository for its unattended method. No human is available: never ask a question and
never wait for approval.

The "Setup unit" section near the end of this prompt gives the mode (`draft` or `apply`), the
owner's goal for the project, the owner's feedback on an earlier draft (if any) and the draft files
already present. Do only the part below that matches the mode.

Language: `notes.md` and the `unit` and `summary` of last-unit.json are written in Brazilian
Portuguese (pt-BR): they go to the owner. Commit messages are in English.

The draft folder is `.superpowers/autopilot/setup/`. Git ignores it in this clone.

## Draft unit

1. Dispatch the `planner` to read the repository and the goal: the stack; the test, lint and build
   commands (package files, Makefile, CI files); the Docker files; the existing docs (`README`,
   `AGENTS.md`, `CLAUDE.md`, `docs/`); and the git branches and remotes (`git branch -a`,
   `git remote -v`). If there is feedback, the planner revises the draft files already there instead
   of starting over.
2. The planner writes, only inside `.superpowers/autopilot/setup/`:
   - `autopilot.md`: the proposed `docs/autopilot.md`. It opens by saying that it holds the rules
     for the sessions the turno scheduler starts ("Autopilot unit"), run unattended one unit per
     session, and that everything in `AGENTS.md` still applies except where this file says
     otherwise. Then these sections:
     - **Backlog**: which file holds the backlog; how items are sized (one spec each, with a
       one-line reason and a status: `Not started`, `In progress`, `Done`); work on the first item
       that is not `Done`; what to do when every item is `Done` (end the unit as `idle`, unless the
       goal asks for more); and that the orchestrator rewrites `.superpowers/autopilot/backlog.json`
       from it at the end of every unit (it lives outside git).
     - **Where things go**: a table with the paths of specs, plans, ledgers
       (`.superpowers/sdd/<topic>/progress.md`) and the decisions-to-review file. Prefer
       `docs/superpowers/specs/`, `docs/superpowers/plans/` and
       `docs/superpowers/decisions-for-review.md` unless the project already has its own places.
     - **Verification**: the exact commands that must pass before a task is done, as this
       repository runs them (inside Docker when that is how its tests run). Only commands found in
       the repository; never invent one.
     - **Git**: the branch policy (the current branch, or one branch per item with the merge
       steps), the commit rules (one commit per plan task, in the project's message style), and
       whether to push (only when the repository has a remote and its docs push; never force-push).
     - **What this file overrides**: which "ask first" or "confirm first" rules of `AGENTS.md` (or
       of the project's docs) it relaxes, such as committing and starting a change whose spec and
       plan passed the orchestrator's check; then a "Still forbidden" list: anything that drops
       data, rewriting history, force-push, reading `.env`, removing or skipping a test.
     Write it in the language of the project's existing docs; in English when there are none.
   - `AGENTS.md`: only when the project has no `AGENTS.md`. Short: what the project is, how to run
     and test it, and the conventions found in the code.
   - The initial backlog, at the path `autopilot.md` names, repeated under the draft folder (for
     example `.superpowers/autopilot/setup/docs/superpowers/roadmap.md` for
     `docs/superpowers/roadmap.md`). Items ordered by value and dependency, built from the goal and
     from what the code already has.
   - `notes.md`: for the owner, in pt-BR, at most 15 lines: what was detected, what was assumed,
     and what the owner should check before approving.
3. Check the draft yourself: every section above is there, each verification command exists in the
   repository, and the backlog serves the goal. If something is missing, send the planner back once
   with the list; after that you rule.
4. Touch nothing outside the draft folder and commit nothing. Then finish (see "Finish") with
   status `waiting` and a pt-BR summary of the draft.

## Apply unit

1. Run `git status --porcelain`. If it lists anything, the tree has changes that are not the
   draft's: finish as `blocked` and say which files.
2. If `docs/autopilot.md` already exists, or the draft has an `AGENTS.md` and the project now has
   one too, or any other destination below already exists (except `.gitignore`), finish as
   `blocked` and say which.
3. Copy each draft file to its place: `autopilot.md` → `docs/autopilot.md`, `AGENTS.md` →
   `AGENTS.md`, and every other file except `notes.md` to the same path relative to the repository
   root.
4. If `.gitignore` does not ignore `.superpowers/`, append the line `.superpowers/` to it.
5. Make one commit, `docs: prepare for turno`, on the current branch, with only those files. Do not
   push.
6. Delete `.superpowers/autopilot/setup/`.
7. Finish with status `worked`.

## Never

Weaken, skip or delete a test; read or print `.env`; destructive operations outside the repository;
force-push; rewrite history; drop database volumes.

## Finish

Write `.superpowers/autopilot/last-unit.json`:
`{"status": "worked" | "waiting" | "blocked", "unit": "<what you did>", "summary": "<one paragraph>"}`,
with `unit` and `summary` in pt-BR. Do not write `backlog.json` in a setup unit. Then end your turn.
