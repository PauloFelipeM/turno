Engine notes (Codex):
- The roles are the agents in `~/.codex/agents/`: `planner`, `plan-reviewer`, `implementer`,
  `reviewer`. Check the spawn allowlist before passing a model; a model outside it is unavailable.
- After a resume, use `list_agents` to find subagents that may still be alive.
