Autopilot unit — project {project}, resumed after an interruption (usage limit, error or an early end
of turn). No human is available.

Reconcile before acting: `git status`, `git log --oneline -10`, the active ledger, and the
subagents that may still be alive (see the engine notes). A task that was mid-way may have uncommitted work; inspect it
before dispatching anyone again.

Then finish the SAME unit you were doing, following the original autopilot instructions (steps 3 to
8), write `.superpowers/autopilot/last-unit.json` and `.superpowers/autopilot/backlog.json`, and end your turn. Everything the owner reads
(decision texts, and `unit`, `summary` and `milestone` in last-unit.json) is written in pt-BR.

In a setup unit (the prompt ends with a "Setup unit" section), the original instructions are the
setup instructions for its mode (draft or apply), not steps 3 to 8: follow them, write
`.superpowers/autopilot/last-unit.json` and do not write `backlog.json`.
