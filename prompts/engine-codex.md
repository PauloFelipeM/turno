Engine notes (Codex):
- The roles are the agents `planner`, `implementer` and `reviewer`. The scheduler writes their files
  (model and effort included) before each unit. Check the spawn allowlist before passing a model; a
  model outside it is unavailable.
- After a resume, use `list_agents` to find subagents that may still be alive.
