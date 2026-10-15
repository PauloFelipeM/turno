Engine notes (Claude Code):
- Dispatch each role with the Agent tool, `subagent_type` equal to the role name: `planner`,
  `implementer`, `reviewer`. The scheduler defines them for this session; do not
  use other agent types for this work.
- Never use AskUserQuestion, plan mode or any tool that waits for a person. Owner decisions go
  through the decisions file (step 6).
- Each role has its own model, set in the scheduler's config. If a role call fails because its
  model reached its own usage limit, do not switch models. Stop, update the ledger and end the
  unit as `blocked`, with a summary saying which role and which model hit the limit, so the owner
  can change the model in the panel or wait for the reset.
- After a resume, the subagents from before the interruption are gone: reconcile from git and the
  ledger, then dispatch again.
- The session may be cut at the end of the weekend window. Commit work as soon as it is green and
  keep the ledger current, so a cut loses as little as possible.
